#include "../../mods/hordes-forever/server/HordesRules.h"
#include <cassert>
#include <iostream>
#include <set>
using namespace HordesForever;
int main()
{
    Progress p(42);
    assert(!p.Pick(0, 1));
    p.Gain(12);
    assert(p.level == 2 && p.choices == 1 && p.xp == 0);
    assert(std::set<uint32_t>(p.offers.begin(), p.offers.end()).size() == 3);
    auto serial = p.serial, card = p.offers[0];
    assert(!p.Pick(serial + 1, 1) && !p.Pick(serial, 0) && !p.Pick(serial, 4));
    assert(p.Pick(serial, 1) && p.ranks[card - 1] == 1);
    assert(!p.Pick(serial, 1) && p.choices == 0);
    p.Gain(100);
    assert(p.choices > 1);
    serial = p.serial;
    assert(p.Pick(serial, 2));
    assert(!p.Pick(serial, 3));
    for (uint32_t seed = 0; seed != 100; ++seed)
    {
        Progress a(seed), b(seed);
        a.Gain(100000); b.Gain(100000);
        assert(a.level == 40 && a.choices == 39 && a.offers == b.offers);
        while (a.choices)
        {
            std::set<uint32_t> shown;
            for (auto offer : a.offers)
                if (offer) { assert(offer <= 6 && a.ranks[offer - 1] < 8); shown.insert(offer); }
            auto remaining = std::count_if(a.ranks.begin(), a.ranks.end(), [](auto rank){ return rank < 8; });
            assert(shown.size() == std::min<size_t>(3, remaining));
            assert(a.Pick(a.serial, 1)); assert(b.Pick(b.serial, 1));
            assert(a.ranks == b.ranks && a.offers == b.offers);
        }
        for (auto rank : a.ranks) assert(rank <= 8);
        assert(a.AttackMs() >= 250);
    }
    p.elapsed = 20 * WaveMs;
    assert(p.Wave() == FinalWave && p.SpawnMs() > 0 && p.MobHealth(true) > p.MobHealth(false));
    std::cout << "PASS: run XP, distinct card offers, stale/double-pick rejection, seeded replay and rank caps\n";
}
