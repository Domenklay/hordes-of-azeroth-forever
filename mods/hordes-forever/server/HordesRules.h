// Hordes Forever survivor prototype. GPL-2.0-or-later.
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <random>
#include <vector>

namespace HordesForever
{
enum class Card : uint32_t { Might = 1, Tempo, Orbit, Chain, Vitality, Regen };
constexpr uint32_t Protocol = 1;
constexpr uint32_t FinalWave = 12;
constexpr uint32_t WaveMs = 20000;
constexpr uint32_t LimitMs = 300000;
constexpr uint32_t MaxMobs = 64;

struct Progress
{
    uint32_t elapsed = 0, kills = 0, level = 1, xp = 0, choices = 0, serial = 0;
    std::array<uint32_t, 6> ranks{};
    std::array<uint32_t, 3> offers{};
    std::mt19937 random;
    explicit Progress(uint32_t seed) : random(seed) { }
    uint32_t Wave() const { return std::min(FinalWave, 1 + elapsed / WaveMs); }
    uint32_t Need() const { return 8 + 4 * level; }
    uint32_t Rank(Card card) const { return ranks[uint32_t(card) - 1]; }
    uint32_t AttackMs() const { return 750 - 60 * Rank(Card::Tempo); }
    uint32_t Damage() const { return 24 + 6 * Rank(Card::Might); }
    uint32_t SpawnMs() const { return 1250 - 60 * Wave(); }
    uint32_t MobHealth(bool boss) const { return boss ? 1500 : 32 + 10 * Wave(); }
    uint32_t MobDamage(bool boss) const { return boss ? 45 : 5 + 2 * Wave(); }
    void Offer()
    {
        std::vector<uint32_t> eligible;
        for (uint32_t i = 0; i != ranks.size(); ++i)
            if (ranks[i] < 8) eligible.push_back(i + 1);
        std::shuffle(eligible.begin(), eligible.end(), random);
        offers.fill(0);
        for (size_t i = 0; i < std::min(eligible.size(), offers.size()); ++i) offers[i] = eligible[i];
        ++serial;
    }
    void Gain(uint32_t amount)
    {
        xp += amount;
        while (level < 40 && xp >= Need())
        {
            xp -= Need(); ++level; ++choices;
        }
        if (choices && offers[0] == 0) Offer();
    }
    bool Pick(uint32_t nonce, uint32_t index)
    {
        if (!choices || nonce != serial || index < 1 || index > offers.size()) return false;
        uint32_t selected = offers[index - 1];
        if (!selected || ranks[selected - 1] >= 8) return false;
        ++ranks[selected - 1]; --choices; offers.fill(0);
        if (choices) Offer();
        return true;
    }
};
}
