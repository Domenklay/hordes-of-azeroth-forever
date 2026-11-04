local unpackValues = unpack or table.unpack
getn = table.getn or function(value) return #value end
tinsert = table.insert
strupper = string.upper

dofile("Interface/GlueXML/BonkRaceCatalog.lua")
local selected = 2
local catalog = {
    { "Human", "HUMAN", { "Воин", "WARRIOR", "Маг", "MAGE" } },
    { "Skyborne", "SKYBORNE", { "Воин", "WARRIOR" } },
    { "Tauren", "TAUREN", { "Воин", "WARRIOR", "Друид", "DRUID" } },
}
GetAvailableRaces = function()
    local result = {}
    for _, race in ipairs(catalog) do
        result[#result + 1] = race[1]
        result[#result + 1] = race[2]
    end
    return unpackValues(result)
end
GetSelectedRace = function() return selected end
SetSelectedRace = function(value) selected = value end
SetCharacterRace = function(value) assert(value == selected) end
GetClassesForRace = function() return unpackValues(catalog[selected][3]) end
local classes = { { token = "WARRIOR", races = { "HUMAN" } }, { token = "MAGE", races = { "HUMAN" } } }
local names, displayNames = {}, {}
local tokens = { WARRIOR = "WARRIOR", MAGE = "MAGE" }
local translations = setmetatable({ Skyborne = "Небесный эльф" }, { __index = function(_, value) return value end })
assert(BonkRaceCatalog.Read(classes, names, displayNames, tokens, translations))
assert(selected == 2)
assert(#classes[1].races == 3 and classes[1].races[2] == "SKYBORNE")
assert(#classes[2].races == 1 and classes[2].races[1] == "HUMAN")
assert(tokens["Воин"] == "WARRIOR")
assert(tokens["Маг"] == "MAGE")
assert(tokens["Друид"] == nil)
assert(displayNames.SKYBORNE == "Небесный эльф")
assert(names.SKYBORNE == "Skyborne")
assert(BonkRaceCatalog.Read(classes, names, displayNames, tokens, translations))
assert(#classes[1].races == 3)
catalog = {}
assert(not BonkRaceCatalog.Read(classes, names, displayNames, tokens, translations))
assert(#classes[1].races == 3 and selected == 2)

local language = "ruRU"
RegisterCVar = function() end
GetCVar = function() return language end
StaticPopupDialogs = {}
dofile("Interface/AddOns/HordesOfAzeroth/VanillaBonkLocale.lua")
dofile("Interface/AddOns/HordesOfAzeroth/VanillaBonkLocaleRuRU.lua")
assert(VanillaBonkL["Warrior"] == "Воин")
assert(VanillaBonkL("Stage {1}/{2}", 2, 5) == "Этап 2/5")
assert(VanillaBonkL["untranslated::Fallback"] == "Fallback")
assert(VanillaBonkLocale.Text("enUS", "Warrior") == "Warrior")
assert(VanillaBonkLocale.English("Воин") == "Warrior")
assert(VanillaBonkLocale.Text("zhCN", "Warrior") == "Warrior")
dofile("Interface/GlueXML/BonkGlueLocale.lua")
dofile("Interface/GlueXML/BonkGlueLocaleRuRU.lua")
assert(BonkGlueL["Choose your character"] == "Выберите персонажа")
assert(BonkGlueL["Skyborne"] == "Небесный эльф")
assert(BonkGlueL("Create {1}", "Воин") == "Создать: Воин")
print("PASS: race discovery, class restrictions, Russian class tokens, retry, selection restoration, localization and English fallback")
