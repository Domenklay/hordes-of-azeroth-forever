-- GPL-2.0-or-later. The server owns all combat, progression and rewards.
local REQUEST, RESPONSE = "HOAQ1", "HOAF1"
local matched, running, nonce = false, false, 0
local received, attempts = false, 0
local transportError
local cards = {
    [1] = { "Тёмная мощь", "+6 к урону автоматического снаряда за ранг." },
    [2] = { "Быстрые руки", "Снаряды вылетают на 60 мс чаще за ранг." },
    [3] = { "Магический круг", "Автоматический урон ближайшим врагам вокруг тебя. Ранги усиливают круг." },
    [4] = { "Цепная молния", "Снаряды поражают дополнительные цели. Ранги увеличивают число целей." },
    [5] = { "Живучесть", "+20% начального здоровья забега за ранг." },
    [6] = { "Восстановление", "Восстанавливает 0,5% максимального здоровья в секунду за ранг." },
}
local function text(parent, font, value, x, y)
    local label = parent:CreateFontString(nil, "OVERLAY", font or "GameFontNormal")
    label:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
    label:SetText(value)
    return label
end
local function panel(name, width, height)
    local frame = CreateFrame("Frame", name, UIParent, "BackdropTemplate")
    frame:SetSize(width, height)
    frame:SetPoint("CENTER")
    frame:SetBackdrop({ bgFile = "Interface/Buttons/WHITE8X8", edgeFile = "Interface/Tooltips/UI-Tooltip-Border", edgeSize = 16,
        insets = { left = 4, right = 4, top = 4, bottom = 4 } })
    frame:SetBackdropColor(0.035, 0.025, 0.06, 0.96)
    frame:SetBackdropBorderColor(0.65, 0.35, 0.85, 1)
    frame:Hide()
    return frame
end
local function button(parent, label, width, x, y, click)
    local result = CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
    result:SetSize(width, 30)
    result:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
    result:SetText(label)
    result:SetScript("OnClick", click)
    return result
end
local function send(message)
    local function report(error)
        if transportError then transportError(error) end
    end
    if C_ChatInfo and C_ChatInfo.SendAddonMessage then
        -- Both prefixes are registered before sending. SERVER deliberately
        -- avoids Forever's unreliable self-whisper path; the core routes this
        -- prefix directly to the sending session's player, not a named target.
        if C_ChatInfo.RegisterAddonMessagePrefix then
            C_ChatInfo.RegisterAddonMessagePrefix(REQUEST)
            C_ChatInfo.RegisterAddonMessagePrefix(RESPONSE)
        end
        local ok, result = pcall(C_ChatInfo.SendAddonMessage, REQUEST, message, "WHISPER", "SERVER")
        local success = Enum and Enum.SendAddonMessageResult and Enum.SendAddonMessageResult.Success
        if not ok or result == false or (success ~= nil and type(result) == "number" and result ~= success) then
            report("Не удалось отправить запрос режиму. Код: " .. tostring(result))
            return false
        end
    elseif SendAddonMessage then
        local ok, error = pcall(SendAddonMessage, REQUEST, message, "WHISPER", "SERVER")
        if not ok then report(tostring(error)); return false end
    else
        report("В клиенте недоступна отправка сообщений аддона."); return false
    end
    return true
end

local menu = panel("HordesForeverMenu", 580, 350)
menu:SetFrameStrata("DIALOG")
text(menu, "GameFontNormalLarge", "Hordes Forever • Вампирлайк", 24, -24)
text(menu, "GameFontHighlight", "Первый одиночный прототип для Forever", 24, -55)
local explanation = text(menu, "GameFontHighlight", "Переживи 12 волн и победи Владыку арены.\nДвигайся WASD: атаки выполняются автоматически.\nЗа опыт выбирай улучшения; в конце вернёшься обратно.", 24, -85)
explanation:SetJustifyH("LEFT")
local info = text(menu, "GameFontNormal", "Подключение к режиму Hordes…", 24, -150)
info:SetWidth(530)
info:SetJustifyH("LEFT")
local records = text(menu, "GameFontHighlight", "", 24, -205)
local start = button(menu, "Начать забег", 240, 24, -252, function()
    if matched and not running and send("START") then info:SetText("Переход на арену…") end
end)
start:Disable()
button(menu, "Закрыть", 240, 310, -252, function() menu:Hide() end)
button(menu, "Проверить связь", 240, 24, -294, function()
    received = false; attempts = 0
    info:SetText("Проверка связи с режимом Hordes…")
    send("HELLO")
end)
transportError = function(message) received = true; info:SetText(message); menu:Show() end
table.insert(UISpecialFrames, "HordesForeverMenu")

local hud = panel("HordesForeverHUD", 460, 160)
hud:ClearAllPoints()
hud:SetPoint("TOP", UIParent, "TOP", 0, -65)
text(hud, "GameFontNormalLarge", "Hordes Forever", 18, -16)
local clock = text(hud, "GameFontHighlight", "", 18, -46)
local score = text(hud, "GameFontHighlight", "", 18, -70)
local hp = CreateFrame("StatusBar", nil, hud)
hp:SetPoint("TOPLEFT", hud, "TOPLEFT", 18, -96)
hp:SetSize(420, 17)
hp:SetStatusBarTexture("Interface/Buttons/WHITE8X8")
hp:SetStatusBarColor(0.25, 0.75, 0.38)
local hpText = text(hud, "GameFontHighlightSmall", "", 22, -98)
local xp = CreateFrame("StatusBar", nil, hud)
xp:SetPoint("TOPLEFT", hud, "TOPLEFT", 18, -120)
xp:SetSize(420, 13)
xp:SetStatusBarTexture("Interface/Buttons/WHITE8X8")
xp:SetStatusBarColor(0.65, 0.3, 0.9)
local xpText = text(hud, "GameFontHighlightSmall", "", 22, -121)
button(hud, "Выйти", 88, 350, -10, function() send("STOP") end)

local choice = panel("HordesForeverChoices", 760, 250)
choice:SetFrameStrata("DIALOG")
text(choice, "GameFontNormalLarge", "Новый уровень! Выбери улучшение", 24, -24)
text(choice, "GameFontHighlight", "Время и атаки врагов приостановлены до выбора.", 24, -52)
local choices = {}
for index = 1, 3 do
    local selected = index
    local pick = button(choice, "", 228, 24 + (index - 1) * 242, -90, function()
        if matched and running then send("PICK " .. nonce .. " " .. selected) end
    end)
    local description = text(choice, "GameFontHighlightSmall", "", 28 + (index - 1) * 242, -136)
    description:SetWidth(216)
    description:SetJustifyH("LEFT")
    choices[index] = { button = pick, description = description }
end

local outcomes = { WIN = "Победа!", DEAD = "Забег окончен: герой пал.", STOP = "Забег завершён.",
    LEFT = "Ты покинул арену.", TIME = "Время вышло.", TRANSFER = "Не удалось перейти на арену.", LOGOUT = "Забег прерван." }
local function words(message)
    local result = {}
    for value in message:gmatch("%S+") do result[#result + 1] = value end
    return result
end
local function receive(message)
    local command, body = message:match("^(%S+)%s*(.*)$")
    local values = words(body or "")
    if command == "HELLO" then
        received = true
        matched = values[1] == "1"
        if matched then info:SetText("Подключён сервер вампирлайка Hordes Forever."); start:Enable() end
    elseif command == "IDLE" and matched then
        running = false
        records:SetText(string.format("Забегов: %s • Побед: %s • Рекорд волны: %s • Монет: %s",
            values[1] or "0", values[2] or "0", values[3] or "0", values[4] or "0"))
        start:Enable()
    elseif command == "ENTERING" and matched then
        running = true; menu:Hide(); info:SetText("Переход на арену…")
    elseif command == "RUN" and matched then
        local n = {}
        for i = 1, 9 do n[i] = tonumber(values[i]); if not n[i] then return end end
        running = true; start:Disable(); menu:Hide(); hud:Show()
        local seconds = math.floor(n[1] / 1000)
        clock:SetText(string.format("%d:%02d • Волна %d/12 • Убийств: %d", math.floor(seconds/60), seconds%60, n[2], n[3]))
        score:SetText("Уровень забега: " .. n[4] .. " • Атаки автоматические")
        hp:SetMinMaxValues(0, math.max(1, n[8])); hp:SetValue(n[7]); hpText:SetText(n[7] .. " / " .. n[8])
        xp:SetMinMaxValues(0, math.max(1, n[6])); xp:SetValue(n[5]); xpText:SetText("Опыт: " .. n[5] .. " / " .. n[6])
        if n[9] == 0 then choice:Hide() end
    elseif command == "OFFER" and matched and running then
        nonce = tonumber(values[1])
        if not nonce then return end
        for i = 1, 3 do
            local card = cards[tonumber(values[i + 1])]
            if card then
                choices[i].button:SetText(card[1]); choices[i].description:SetText(card[2]); choices[i].button:Enable()
            else
                choices[i].button:SetText("Уже улучшено"); choices[i].description:SetText(""); choices[i].button:Disable()
            end
        end
        choice:Show()
    elseif command == "END" and matched then
        running = false; hud:Hide(); choice:Hide(); menu:Show(); start:Enable()
        info:SetText((outcomes[values[1]] or "Забег завершён.") .. "\nТы возвращаешься к месту старта.")
        records:SetText(string.format("Убийств: %s • Волна: %s • Получено монет: %s", values[2] or "0", values[3] or "0", values[5] or "0"))
    elseif command == "ERROR" then
        received = true
        info:SetText(body); menu:Show()
        if DEFAULT_CHAT_FRAME then DEFAULT_CHAT_FRAME:AddMessage("|cffff7777Hordes:|r " .. body) end
    end
end

local events = CreateFrame("Frame")
events:RegisterEvent("PLAYER_LOGIN")
events:RegisterEvent("PLAYER_ENTERING_WORLD")
events:RegisterEvent("CHAT_MSG_ADDON")
events:SetScript("OnEvent", function(_, event, prefix, message, channel, sender)
    if event == "PLAYER_LOGIN" then
        if C_ChatInfo and C_ChatInfo.RegisterAddonMessagePrefix then
            C_ChatInfo.RegisterAddonMessagePrefix(REQUEST)
            C_ChatInfo.RegisterAddonMessagePrefix(RESPONSE)
        end
        menu:Show()
    elseif event == "PLAYER_ENTERING_WORLD" then
        attempts = 0
        local function hello()
            if matched or received or attempts >= 3 then return end
            attempts = attempts + 1
            send("HELLO")
            if not matched and not received and attempts < 3 then C_Timer.After(2, hello) end
        end
        C_Timer.After(1, function()
            if running then send("HELLO") else hello() end
        end)
        C_Timer.After(7, function()
            if not matched and not received then
                info:SetText("Нет ответа от режима. Нажми «Проверить связь». Если связь не появилась, скопируй журнал движка.")
                start:Disable(); menu:Show()
            end
        end)
    elseif event == "CHAT_MSG_ADDON" and prefix == RESPONSE and channel == "WHISPER" then
        local own = UnitName("player")
        if sender and own and (sender == own or sender:match("^([^%-]+)") == own) then receive(message) end
    end
end)
SLASH_HORDESFOREVER1 = "/hordes"
SLASH_HORDESFOREVER2 = "/hordesforever"
SlashCmdList.HORDESFOREVER = function() menu:Show(); send("HELLO") end
