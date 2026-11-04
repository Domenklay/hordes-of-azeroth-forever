const assert = require('node:assert/strict');
const fs = require('node:fs');
const logic = require('../translate/logic.js');
for (const [folder, prefix, toc] of [
    ['Interface/AddOns/HordesOfAzeroth', 'VanillaBonkLocale', 'HordesOfAzeroth.toc'],
    ['Interface/GlueXML', 'BonkGlueLocale', 'GlueXML.toc'],
]) {
    const filename = `${prefix}RuRU.lua`;
    const source = fs.readFileSync(`${folder}/${filename}`, 'utf8');
    const parsed = logic.parseLocaleTable(source);
    assert.equal(parsed.locale, 'ruRU');
    assert.equal(parsed.entries.size, [...source.matchAll(/^    \["/gm)].length, 'duplicate keys');
    for (const [key, translation] of parsed.entries) {
        const english = logic.unescapeLua(key);
        const problems = logic.checkEntry({ english, translation, section: 'addon' });
        assert.deepEqual(problems, [], `${filename}: ${english}`);
    }
    const tocLines = fs.readFileSync(`${folder}/${toc}`, 'utf8').split(/\r?\n/);
    assert(tocLines.indexOf(filename) > tocLines.indexOf(`${prefix}.lua`));
    console.log(`PASS: ${filename}, ${parsed.entries.size} translations, placeholders, formatting and TOC order`);
}
const config = JSON.parse(fs.readFileSync('config/release.json', 'utf8'));
assert.equal(config.repository, 'Domenklay/hordes-of-azeroth-forever');
for (const file of ['tools/launcher/UpdateCheck.cs', 'tools/launcher/HordesOfAzerothLauncher.csproj']) {
    assert(!fs.readFileSync(file, 'utf8').includes('jonhaldors/hordes-of-azeroth'));
}
console.log('PASS: release and launcher use the fork repository');
