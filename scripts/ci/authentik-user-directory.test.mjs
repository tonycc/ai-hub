import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';

const template = readFileSync(new URL('../../deploy/authentik/templates/if/user.html', import.meta.url), 'utf8');
const source = template.match(/<script>([\s\S]*?)<\/script>/)[1]
    .replace('{{ request.brand.attributes.ai_hub_portal_url|escapejs }}', 'https://192.168.1.99');
function render(hash, portal = "https://192.168.1.99") {
    const redirects = [];
    const scripts = [];
    const handlers = {};
    const document = { readyState: "complete", body: {}, documentElement: {}, head: { appendChild: s => scripts.push(s) }, createElement: () => ({}) };
    const location = { hash, origin: 'https://192.168.1.99:8443', replace: url => redirects.push(url) };
    runInNewContext(source.replace("https://192.168.1.99", portal), { URL, document, window: { location, addEventListener: (name, fn) => { handlers[name] = fn; } } });
    return { redirects, scripts, document, location, handlers };
}
for (const hash of ['', '#', '#/', '#/library', '#/library/', '#/library?search=dsh-work']) {
    test(`directory ${hash} redirects without loading the application dashboard`, () => {
        const result = render(hash);
        assert.deepEqual(result.redirects, ['https://192.168.1.99/#']);
        assert.equal(result.scripts.length, 0);
        assert.equal(result.document.documentElement.hidden, true);
    });
}
test('account settings retain upstream UI, and navigation back to library redirects', () => {
    const result = render('#/settings;page=security');
    assert.equal(result.scripts.length, 1);
    assert.equal(result.scripts[0].type, 'module');
    assert.equal(result.document.documentElement.hidden, false);
    assert.deepEqual(result.redirects, []);
    result.location.hash = '#/library';
    result.handlers.hashchange();
    assert.equal(result.document.documentElement.hidden, true);
    assert.equal(result.redirects.length, 1);
});
test('template is limited to user interface and preserves upstream versioned assets', () => {
    assert.match(template, /versioned_script 'dist\/user\/UserInterface-%v.js'/);
    const compose = readFileSync(new URL('../../deploy/compose.yaml', import.meta.url), 'utf8');
    assert.match(compose.split("\n  authentik-server:\n")[1].split("\n  authentik-worker:\n")[0], /target: \/templates\/if\/user.html/);
    assert.doesNotMatch(compose, /target: \/templates\/(?:if\/flow|base\/skeleton)/);
});

test('missing or invalid portal configuration gives an error without loading the directory', () => {
    for (const portal of ['', 'javascript:alert(1)', 'https://192.168.1.99:8443']) {
        const result = render('#/library', portal);
        assert.equal(result.document.documentElement.hidden, false);
        assert.match(result.document.body.textContent, /门户地址未正确配置/);
        assert.equal(result.scripts.length, 0);
        assert.equal(result.redirects.length, 0);
    }
});
