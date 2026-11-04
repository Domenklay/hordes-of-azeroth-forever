BonkRaceCatalog = {};

function BonkRaceCatalog.Read(classes, names, displayNames, classTokens, translate)
    local races = { GetAvailableRaces() };
    local count = getn(races) / 2;
    if ( count == 0 ) then
        return false;
    end
    local available = {};
    local savedRace = GetSelectedRace();
    for index = 1, count do
        local name = races[index * 2 - 1];
        local token = strupper(races[index * 2]);
        names[token] = names[token] or name;
        displayNames[token] = translate[names[token]];
        SetSelectedRace(index);
        SetCharacterRace(index);
        local raceClasses = { GetClassesForRace() };
        for classIndex = 1, getn(raceClasses) / 2 do
            local className = raceClasses[classIndex * 2 - 1];
            local classToken = strupper(raceClasses[classIndex * 2]);
            if ( classTokens[classToken] == classToken ) then
                classTokens[className] = classToken;
                available[classToken] = available[classToken] or {};
                tinsert(available[classToken], token);
            end
        end
    end
    if ( savedRace and savedRace > 0 and savedRace <= count ) then
        SetSelectedRace(savedRace);
        SetCharacterRace(savedRace);
    end
    for _, class in ipairs(classes) do
        if ( available[class.token] ) then
            class.races = available[class.token];
        end
    end
    return true;
end
