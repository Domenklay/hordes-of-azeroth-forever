// Hordes Forever survivor prototype for TrinityCore Forever. GPL-2.0-or-later.
#include "HordesRules.h"
#include "ScriptMgr.h"
#include "Chat.h"
#include "ChatCommand.h"
#include "ChatPackets.h"
#include "Config.h"
#include "Creature.h"
#include "CreatureAI.h"
#include "DatabaseEnv.h"
#include "Log.h"
#include "Map.h"
#include "MotionMaster.h"
#include "ObjectAccessor.h"
#include "ObjectMgr.h"
#include "Player.h"
#include "RBAC.h"
#include "SpellInfo.h"
#include "SpellMgr.h"
#include "TemporarySummon.h"
#include "WorldSession.h"
#include <atomic>
#include <chrono>
#include <cmath>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>

namespace HordesForever
{
constexpr uint32 EnemyEntry = 900950, BossEntry = 900951;
constexpr uint32 ArenaMap = 0;
// Gurubashi arena floor, classic map 0. Run exit restores the saved origin.
constexpr float ArenaX = -13204.5f, ArenaY = 272.9f, ArenaZ = 21.85f, Radius = 38.0f;
constexpr char RequestPrefix[] = "HOAQ1", ResponsePrefix[] = "HOAF1";
std::atomic<bool> Ready{false};
std::recursive_mutex Mutex;

struct Run
{
    Progress progress;
    ObjectGuid owner, boss;
    std::set<ObjectGuid> mobs;
    WorldLocation origin;
    uint64 oldHealth = 0, oldMaxHealth = 0;
    uint32 attack = 0, spawn = 0, regen = 0, snapshot = 0, enteringMs = 0;
    bool entering = true, dealing = false, finalSpawned = false, won = false, ending = false;
    explicit Run(Player* p) : progress(uint32(std::chrono::steady_clock::now().time_since_epoch().count())),
        owner(p->GetGUID()), origin(p->GetWorldLocation()), oldHealth(p->GetHealth()), oldMaxHealth(p->GetMaxHealth()) { }
};
std::map<uint64, std::unique_ptr<Run>> Runs;
std::map<ObjectGuid, WorldLocation> Returning;
std::map<uint64, std::chrono::steady_clock::time_point> LastRequest;

Run* Find(Player* p)
{
    auto i = Runs.find(p->GetGUID().GetCounter());
    return i == Runs.end() ? nullptr : i->second.get();
}

void Send(Player* p, std::string const& text)
{
    WorldPackets::Chat::Chat packet;
    packet.Initialize(CHAT_MSG_WHISPER, LANG_ADDON, p, p, text, 0, "", LOCALE_enUS, ResponsePrefix);
    p->GetSession()->SendPacket(packet.Write());
}
void Error(Player* p, std::string const& text) { Send(p, "ERROR " + text); }

void Snapshot(Player* p, Run* run)
{
    if (!run)
    {
        uint32 runs = 0, wins = 0, best = 0;
        uint64 coins = 0;
        if (auto row = CharacterDatabase.PQuery("SELECT runs,wins,best_wave,coins FROM hoa_forever_progress WHERE guid={}", p->GetGUID().GetCounter()))
        { auto f = row->Fetch(); runs=f[0].GetUInt32(); wins=f[1].GetUInt32(); best=f[2].GetUInt32(); coins=f[3].GetUInt64(); }
        Send(p, Trinity::StringFormat("IDLE {} {} {} {}", runs, wins, best, coins));
        return;
    }
    auto const& r = run->progress;
    Send(p, Trinity::StringFormat("RUN {} {} {} {} {} {} {} {} {}", r.elapsed, r.Wave(), r.kills,
         r.level, r.xp, r.Need(), p->GetHealth(), p->GetMaxHealth(), r.choices));
    if (r.choices)
        Send(p, Trinity::StringFormat("OFFER {} {} {} {}", r.serial, r.offers[0], r.offers[1], r.offers[2]));
}

void RememberOrigin(Player* p, Run const& run)
{
    CharacterDatabase.DirectExecute(Trinity::StringFormat(
        "REPLACE INTO hoa_forever_return (guid,map,x,y,z,o) VALUES ({},{},{},{},{},{})",
        p->GetGUID().GetCounter(), run.origin.GetMapId(), run.origin.GetPositionX(), run.origin.GetPositionY(),
        run.origin.GetPositionZ(), run.origin.GetOrientation()).c_str());
}

void Finish(Player* p, char const* result, bool returnNow = true)
{
    auto i = Runs.find(p->GetGUID().GetCounter());
    if (i == Runs.end() || i->second->ending) return;
    Run& run = *i->second; run.ending = true;
    for (auto guid : run.mobs)
        if (p->FindMap())
            if (auto creature = ObjectAccessor::GetCreature(*p, guid)) creature->DespawnOrUnsummon();
    run.mobs.clear();
    uint32 reward = run.progress.kills / 5 + run.progress.Wave() * 2 + (run.won ? 100 : 0);
    CharacterDatabase.DirectExecute(Trinity::StringFormat(
        "INSERT INTO hoa_forever_progress (guid,runs,wins,kills,best_wave,coins) VALUES ({},1,{},{},{},{}) "
        "ON DUPLICATE KEY UPDATE runs=runs+1,wins=wins+VALUES(wins),kills=kills+VALUES(kills),"
        "best_wave=GREATEST(best_wave,VALUES(best_wave)),coins=coins+VALUES(coins)",
        p->GetGUID().GetCounter(), run.won ? 1 : 0, run.progress.kills, run.progress.Wave(), reward).c_str());
    if (!p->IsAlive()) { p->ResurrectPlayer(1.0f, false); p->SpawnCorpseBones(); }
    p->CombatStop(true);
    p->UpdateAllStats();
    p->SetHealth(std::max<uint64>(1, std::min(run.oldHealth, p->GetMaxHealth())));
    Send(p, Trinity::StringFormat("END {} {} {} {} {}", result, run.progress.kills, run.progress.Wave(), run.progress.elapsed, reward));
    WorldLocation origin = run.origin;
    Runs.erase(i);
    if (returnNow)
    {
        Returning[p->GetGUID()] = origin;
        p->TeleportTo(origin);
    }
}

void Credit(Player* p, Creature* creature)
{
    Run* run = Find(p);
    if (!run || run->ending || !run->mobs.erase(creature->GetGUID())) return;
    bool boss = creature->GetGUID() == run->boss;
    ++run->progress.kills;
    run->progress.Gain(boss ? 20 : 1);
    if (boss) run->won = true;
    creature->DespawnOrUnsummon(std::chrono::milliseconds(1000));
    Snapshot(p, run);
}

class EnemyAI final : public CreatureAI
{
    ObjectGuid owner;
    uint32 cooldown = 1000;
    bool paused = false;
public:
    EnemyAI(Creature* creature, ObjectGuid player) : CreatureAI(creature), owner(player) { }
    void UpdateAI(uint32 diff) override
    {
        std::lock_guard<std::recursive_mutex> lock(Mutex);
        Player* p = ObjectAccessor::GetPlayer(*me, owner);
        Run* run = p ? Find(p) : nullptr;
        if (!p || !run || run->ending || !p->IsAlive() || !run->mobs.count(me->GetGUID()))
        { me->DespawnOrUnsummon(); return; }
        if (run->progress.choices || run->entering)
        {
            if (!paused) { me->GetMotionMaster()->Clear(); me->StopMoving(); paused = true; }
            return;
        }
        if (paused || me->GetVictim() != p)
        { paused = false; me->Attack(p, false); me->GetMotionMaster()->MoveChase(p); }
        if (cooldown > diff) { cooldown -= diff; return; }
        cooldown = 1000;
        if (me->IsWithinMeleeRange(p))
            Unit::DealDamage(me, p, run->progress.MobDamage(me->GetEntry() == BossEntry), nullptr,
                             DIRECT_DAMAGE, SPELL_SCHOOL_MASK_NORMAL, nullptr, false);
    }
};

class Enemies final : public CreatureScript
{
public:
    Enemies() : CreatureScript("hordes_forever_enemy") { }
    CreatureAI* GetAI(Creature* creature) const override
    {
        auto summon = creature->ToTempSummon();
        return new EnemyAI(creature, summon ? summon->GetSummonerGUID() : ObjectGuid::Empty);
    }
};

void Spawn(Player* p, Run& run, bool boss)
{
    std::uniform_real_distribution<float> angle(0, 6.2831853f);
    float a = angle(run.progress.random), distance = boss ? 28.0f : 20.0f + float(run.progress.random() % 15);
    Position pos(ArenaX + std::cos(a)*distance, ArenaY + std::sin(a)*distance, ArenaZ, a);
    if (auto creature = p->SummonCreature(boss ? BossEntry : EnemyEntry, pos, TEMPSUMMON_MANUAL_DESPAWN,
                                        std::chrono::milliseconds(0), 0, 0, p->GetGUID()))
    {
        creature->SetFaction(14);
        creature->SetLevel(p->GetLevel());
        creature->SetMaxHealth(run.progress.MobHealth(boss));
        creature->SetFullHealth();
        creature->SetRegenerateHealth(false);
        creature->SetCanHaveLoot(false);
        creature->SetLootMode(0);
        creature->SetNoCallAssistance(true);
        creature->SetReactState(REACT_PASSIVE);
        creature->SetSpeedRate(MOVE_RUN, boss ? 0.8f : 0.65f);
        creature->SetCorpseDelay(1);
        creature->AI()->AttackStart(p);
        run.mobs.insert(creature->GetGUID());
        if (boss) run.boss = creature->GetGUID();
    }
}

void Hit(Player* p, Run& run, Creature* creature, uint32 damage, uint32 visualSpell)
{
    if (!creature || !creature->IsAlive()) return;
    if (auto spell = sSpellMgr->GetSpellInfo(visualSpell, DIFFICULTY_NONE))
        if (uint32 visual = spell->GetSpellVisual(p)) p->SendPlaySpellVisual(creature, visual, 0, 0, 28.0f);
    run.dealing = true;
    Unit::DealDamage(p, creature, damage, nullptr, SPELL_DIRECT_DAMAGE, SPELL_SCHOOL_MASK_ARCANE, nullptr, false);
    run.dealing = false;
}

void Tick(Player* p, uint32 diff)
{
    Run* run = Find(p);
    if (!run) return;
    if (run->entering)
    {
        run->enteringMs += diff;
        if (p->IsBeingTeleported()) return;
        if (p->GetMapId() != ArenaMap || p->GetDistance2d(ArenaX, ArenaY) > 10)
        { if (run->enteringMs > 15000) Finish(p, "TRANSFER"); return; }
        run->entering = false;
        p->SetMaxHealth(std::max<uint64>(600, run->oldMaxHealth)); p->SetFullHealth();
    }
    if (!p->IsAlive()) { Finish(p, "DEAD"); return; }
    if (p->GetMapId() != ArenaMap || p->GetDistance2d(ArenaX, ArenaY) > Radius + 5)
    { Finish(p, "LEFT"); return; }
    if (run->won) { Finish(p, "WIN"); return; }
    if (!run->progress.choices)
    {
        run->progress.elapsed += diff;
        if (run->progress.elapsed >= LimitMs) { Finish(p, "TIME"); return; }
        if (run->progress.Wave() == FinalWave && !run->finalSpawned)
        { run->finalSpawned = true; Spawn(p, *run, true); }
        run->spawn += diff;
        if (run->spawn >= run->progress.SpawnMs())
        {
            run->spawn = 0;
            if (run->mobs.size() < MaxMobs) Spawn(p, *run, false);
        }
        run->attack += diff;
        if (run->attack >= run->progress.AttackMs())
        {
            run->attack = 0;
            std::vector<Creature*> targets;
            for (auto guid : run->mobs)
                if (auto creature = ObjectAccessor::GetCreature(*p, guid))
                    if (creature->IsAlive()) targets.push_back(creature);
            std::sort(targets.begin(), targets.end(), [p](Creature* a, Creature* b){ return p->GetDistance(a) < p->GetDistance(b); });
            uint32 hits = 1 + run->progress.Rank(Card::Chain) / 2;
            if (run->progress.Rank(Card::Chain)) ++hits;
            for (size_t i = 0; i < std::min<size_t>(hits, targets.size()); ++i)
                if (p->GetDistance(targets[i]) <= 30)
                    Hit(p, *run, targets[i], i ? run->progress.Damage()/2 : run->progress.Damage(), i ? 403 : 686);
            uint32 orbit = run->progress.Rank(Card::Orbit);
            if (orbit)
                for (auto creature : targets)
                    if (creature->IsAlive() && p->GetDistance(creature) <= 4.5f + orbit*0.6f)
                        Hit(p, *run, creature, 6 + 5*orbit, 1449);
        }
        run->regen += diff;
        if (run->regen >= 1000)
        {
            run->regen = 0;
            uint64 max = p->GetMaxHealth();
            p->SetHealth(std::min(max, p->GetHealth() + max*run->progress.Rank(Card::Regen)/200));
        }
    }
    run->snapshot += diff;
    if (run->snapshot >= 500) { run->snapshot = 0; Snapshot(p, run); }
}

void Request(Player* p, std::string const& text)
{
    if (!Ready) { Error(p, "Режим Hordes не включён на этом сервере."); return; }
    if (text.size() > 128) return;
    std::lock_guard<std::recursive_mutex> lock(Mutex);
    auto now = std::chrono::steady_clock::now();
    auto& last = LastRequest[p->GetGUID().GetCounter()];
    if (now - last < std::chrono::milliseconds(100)) return;
    last = now;
    std::istringstream input(text); std::string action; input >> action;
    if (action == "HELLO" || action == "status")
    { Send(p, "HELLO 1"); Snapshot(p, Find(p)); }
    else if (action == "STOP" || action == "stop") Finish(p, "STOP");
    else if (action == "START" || action == "start")
    {
        if (Find(p)) { Snapshot(p, Find(p)); return; }
        if (!Runs.empty()) { Error(p, "Тестовый режим рассчитан на один одиночный забег одновременно."); return; }
        if (!p->IsAlive() || p->IsInCombat() || p->IsInFlight() || p->IsBeingTeleported() ||
            p->GetGroup() || p->GetTransport() || p->GetVehicle() || p->GetMap()->IsDungeon() || p->GetMap()->IsBattlegroundOrArena())
        { Error(p, "Для старта выйдите из боя и группы, покиньте подземелье и остановитесь на земле."); return; }
        auto run = std::make_unique<Run>(p);
        RememberOrigin(p, *run);
        p->Dismount();
        if (!p->TeleportTo(ArenaMap, ArenaX, ArenaY, ArenaZ, 0))
        {
            CharacterDatabase.DirectExecute(Trinity::StringFormat("DELETE FROM hoa_forever_return WHERE guid={}", p->GetGUID().GetCounter()).c_str());
            Error(p, "Не удалось перейти на арену."); return;
        }
        Runs.emplace(p->GetGUID().GetCounter(), std::move(run));
        Send(p, "ENTERING");
    }
    else if (action == "PICK")
    {
        uint32 nonce = 0, choice = 0; input >> nonce >> choice;
        Run* run = Find(p);
        if (!run || !run->progress.Pick(nonce, choice)) { Error(p, "Этот выбор уже устарел или недоступен."); return; }
        uint64 maximum = std::max<uint64>(600, run->oldMaxHealth) * (100 + 20*run->progress.Rank(Card::Vitality)) / 100;
        uint64 before = p->GetMaxHealth(); p->SetMaxHealth(maximum);
        p->SetHealth(std::min(maximum, p->GetHealth() + (maximum > before ? maximum-before : maximum/20)));
        Snapshot(p, run);
    }
}

class Players final : public PlayerScript
{
public:
    Players() : PlayerScript("hordes_forever_players") { }
    void OnAddonMessageSent(Player* p, uint32 type, std::string const& prefix, std::string const& text, Player* receiver) override
    { if (type == CHAT_MSG_WHISPER && receiver == p && prefix == RequestPrefix) Request(p, text); }
    void OnCreatureKill(Player* p, Creature* killed) override
    { std::lock_guard<std::recursive_mutex> lock(Mutex); Credit(p, killed); }
    void OnGiveXP(Player*, uint32& amount, Unit* victim) override
    { if (victim && (victim->GetEntry() == EnemyEntry || victim->GetEntry() == BossEntry)) amount = 0; }
    void OnLogout(Player* p) override
    {
        std::lock_guard<std::recursive_mutex> lock(Mutex);
        Finish(p, "LOGOUT", false); Returning.erase(p->GetGUID()); LastRequest.erase(p->GetGUID().GetCounter());
    }
    void OnLogin(Player* p, bool) override
    {
        if (!Ready) return;
        if (auto row = CharacterDatabase.PQuery("SELECT map,x,y,z,o FROM hoa_forever_return WHERE guid={}", p->GetGUID().GetCounter()))
        {
            auto f = row->Fetch();
            if (!p->IsAlive()) p->ResurrectPlayer(1.0f, false);
            WorldLocation origin(f[0].GetUInt32(), f[1].GetFloat(), f[2].GetFloat(), f[3].GetFloat(), f[4].GetFloat());
            std::lock_guard<std::recursive_mutex> lock(Mutex);
            Returning[p->GetGUID()] = origin;
            p->TeleportTo(origin);
        }
        ChatHandler(p->GetSession()).SendSysMessage("Hordes Forever: вампирлайк подключён. Откройте /hordes или /hoa start.");
    }
    void OnDelete(ObjectGuid guid, uint32) override
    {
        if (!Ready) return;
        for (char const* table : {"hoa_forever_progress", "hoa_forever_return"})
            CharacterDatabase.DirectExecute(Trinity::StringFormat("DELETE FROM {} WHERE guid={}", table, guid.GetCounter()).c_str());
    }
};

class Damage final : public UnitScript
{
public:
    Damage() : UnitScript("hordes_forever_damage") { }
    void OnDamage(Unit* attacker, Unit* victim, uint32& damage) override
    {
        if (!Ready || !victim) return;
        std::lock_guard<std::recursive_mutex> lock(Mutex);
        if (Player* p = victim->ToPlayer())
            if (Run* run = Find(p))
                if (run->entering || run->progress.choices || !attacker || !run->mobs.count(attacker->GetGUID())) damage = 0;
        if (victim->GetEntry() != EnemyEntry && victim->GetEntry() != BossEntry) return;
        for (auto const& [id, run] : Runs)
            if (run->mobs.count(victim->GetGUID()) &&
                (!run->dealing || run->progress.choices || !attacker || attacker->GetGUID() != run->owner)) damage = 0;
    }
};

class Startup final : public WorldScript
{
public:
    Startup() : WorldScript("hordes_forever_startup") { }
    void OnUpdate(uint32 diff) override
    {
        if (!Ready) return;
        // WorldScript::OnUpdate runs after MapMgr::Update joins map workers.
        // Keep entity mutation out of another map's worker thread.
        std::lock_guard<std::recursive_mutex> lock(Mutex);
        std::vector<ObjectGuid> players;
        for (auto const& [id, run] : Runs) players.push_back(run->owner);
        for (auto guid : players)
            if (auto p = ObjectAccessor::FindConnectedPlayer(guid))
                if (p->IsInWorld()) Tick(p, diff);
        for (auto i = Returning.begin(); i != Returning.end();)
        {
            Player* p = ObjectAccessor::FindConnectedPlayer(i->first);
            if (p && p->IsInWorld() && !p->IsBeingTeleported())
            {
                auto const& origin = i->second;
                if (p->GetMapId() == origin.GetMapId() && p->GetDistance2d(origin.GetPositionX(), origin.GetPositionY()) < 5)
                {
                    CharacterDatabase.DirectExecute(Trinity::StringFormat("DELETE FROM hoa_forever_return WHERE guid={}", p->GetGUID().GetCounter()).c_str());
                    i = Returning.erase(i); continue;
                }
            }
            ++i;
        }
    }
    void OnWorldInitialized() override
    {
        if (!sConfigMgr->GetBoolDefault("Hordes.Enable", false)) return;
        for (char const* table : {"hoa_forever_progress", "hoa_forever_return"})
            if (!CharacterDatabase.Query(Trinity::StringFormat("SELECT COUNT(*) FROM {}", table).c_str()))
            { TC_LOG_ERROR("scripts", "Hordes schema missing: {}", table); return; }
        for (uint32 entry : {EnemyEntry, BossEntry})
            if (auto creature = sObjectMgr->GetCreatureTemplate(entry); !creature || creature->Models.empty())
            { TC_LOG_ERROR("scripts", "Hordes creature template or model missing: {}", entry); return; }
        Ready = true;
        TC_LOG_INFO("scripts", "HOA_MODE_V1_READY: Hordes Forever survivor prototype enabled");
    }
};

using namespace Trinity::ChatCommands;
class Commands final : public CommandScript
{
public:
    Commands() : CommandScript("hordes_forever_commands") { }
    std::span<ChatCommandBuilder const> GetCommands() const override
    {
        static ChatCommandTable table = {{"hoa", Handle, rbac::RBAC_PERM_COMMAND_COMMANDS, Console::No}};
        return table;
    }
    static bool Handle(ChatHandler* handler, char const* args)
    {
        if (!handler->GetSession()) return false;
        Request(handler->GetSession()->GetPlayer(), args && *args ? args : "status"); return true;
    }
};
}

void AddSC_hordes_forever()
{
    new HordesForever::Startup(); new HordesForever::Players();
    new HordesForever::Damage(); new HordesForever::Commands();
    new HordesForever::Enemies();
}
