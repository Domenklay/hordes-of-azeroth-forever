-- Exercise the live addon protocol with a small WoW UI adapter.
local frames, sent, timers = {}, {}, {}
local registered = {}
local methods = {}
function methods:SetScript(event, fn) self.scripts[event] = fn end
function methods:Show() self.shown = true end
function methods:Hide() self.shown = false end
function methods:Enable() self.enabled = true end
function methods:Disable() self.enabled = false end
function methods:SetText(value) self.label = value end
function methods:CreateFontString() return setmetatable({scripts={}}, {__index=methods}) end
setmetatable(methods, {__index=function() return function() end end})
function CreateFrame(kind, name, parent)
    local frame = setmetatable({scripts={}, enabled=true, parent=parent, name=name}, {__index=methods})
    frames[#frames+1] = frame
    if name then _G[name] = frame end
    return frame
end
UIParent, UISpecialFrames, SlashCmdList = {}, {}, {}
function UnitName() return "Tester" end
C_Timer = { After=function(_, fn) timers[#timers+1] = fn end }
C_ChatInfo = { RegisterAddonMessagePrefix=function(prefix) registered[prefix]=true end,
    SendAddonMessage=function(prefix, text, channel, name)
        assert(registered.HOAQ1 and registered.HOAF1, "both protocol prefixes must be registered before sending")
        assert(prefix=="HOAQ1" and channel=="WHISPER" and name=="SERVER", "requests must use the server endpoint, not a self whisper")
        sent[#sent+1]=text
    end }
dofile("mods/hordes-forever/addon/HordesForever/HordesForever.lua")
local start, events
for _, f in ipairs(frames) do
    if f.label=="Начать забег" then start=f end
    if f.scripts.OnEvent then events=f end
end
local function event(...) events.scripts.OnEvent(events, ...) end
local function response(text, sender) event("CHAT_MSG_ADDON", "HOAF1", text, "WHISPER", sender or "Tester") end
event("PLAYER_LOGIN"); event("PLAYER_ENTERING_WORLD")
for _, timer in ipairs(timers) do timer() end
assert(sent[1]=="HELLO" and not start.enabled, "plain Forever must not enable a fake survivor start")
assert(#sent==3, "initial handshake retries must be bounded")
response("HELLO 1", "OtherPlayer"); assert(not start.enabled)
response("HELLO 1"); response("IDLE 3 1 12 100"); assert(start.enabled)
start.scripts.OnClick(); assert(sent[#sent]=="START")
response("ENTERING"); response("RUN 20000 2 10 2 2 16 600 600 1")
assert(HordesForeverHUD.shown and not HordesForeverMenu.shown)
response("OFFER 42 2 4 5"); assert(HordesForeverChoices.shown)
for _, f in ipairs(frames) do
    if f.label=="Быстрые руки" then f.scripts.OnClick() end
end
assert(sent[#sent]=="PICK 42 1", "selection must carry the authoritative offer serial and position")
response("RUN 20000 2 10 2 2 16 600 600 0"); assert(not HordesForeverChoices.shown)
response("END WIN 123 12 240000 148")
assert(HordesForeverMenu.shown and not HordesForeverHUD.shown and start.enabled)
print("PASS: survivor handshake, sender checks, run HUD, choice nonce and run exit")
