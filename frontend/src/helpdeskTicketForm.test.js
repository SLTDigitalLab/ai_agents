import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    HELPDESK_FORM_CLOSE,
    HELPDESK_FORM_OPEN,
    helpdeskErrorMessage,
    parseHelpdeskFormPayload,
    validateHelpdeskForm,
} from './helpdeskTicketForm.js';

const marker = (json) => `${HELPDESK_FORM_OPEN}${json}${HELPDESK_FORM_CLOSE}`;
const intro = 'Please fill in the form below to create your support ticket.';

test('no marker leaves text untouched', () => {
    assert.deepEqual(parseHelpdeskFormPayload('Hello'), { text: 'Hello', form: null });
    assert.deepEqual(parseHelpdeskFormPayload(), { text: '', form: null });
});

test('complete marker is removed and returns the pre-fill', () => {
    const text = `Checking.\n\n${intro}\n\n${marker('{"description": "Printer not working"}')}`;
    assert.deepEqual(parseHelpdeskFormPayload(text), {
        text: `Checking.\n\n${intro}`,
        form: { description: 'Printer not working' },
    });
});

test('marker still streaming is hidden but form not shown yet', () => {
    const text = `${intro}\n\n${HELPDESK_FORM_OPEN}{"descr`;
    assert.deepEqual(parseHelpdeskFormPayload(text), { text: intro, form: null });
});

test('bad or missing pre-fill still shows an empty form', () => {
    for (const json of ['not json', '{}', '{"description": 42}', '']) {
        assert.deepEqual(parseHelpdeskFormPayload(marker(json)).form, { description: '' }, json);
    }
});

test('pre-fill is capped at 255 characters', () => {
    const long = JSON.stringify({ description: 'a'.repeat(400) });
    assert.equal(parseHelpdeskFormPayload(marker(long)).form.description.length, 255);
});

test('validation mirrors the backend rules', () => {
    const ok = { description: 'Printer broken', incident_type: 'SOA', incident_sub_type: 'Resubmission issue', priority: 'Low' };
    assert.equal(validateHelpdeskForm(ok), '');
    assert.match(validateHelpdeskForm({ ...ok, description: '   ' }), /required/);
    assert.match(validateHelpdeskForm({ ...ok, incident_type: '' }), /required/);
    assert.match(validateHelpdeskForm({ ...ok, incident_sub_type: '' }), /required/);
    assert.match(validateHelpdeskForm({ ...ok, description: 'x'.repeat(256) }), /255/);
    assert.match(validateHelpdeskForm({ ...ok, incident_type: 'x'.repeat(101) }), /100/);
    assert.match(validateHelpdeskForm({ ...ok, priority: 'Urgent' }), /priority/);
});

test('error messages come from the API detail when available', () => {
    assert.equal(helpdeskErrorMessage(504, { detail: 'Took too long.' }), 'Took too long.');
    assert.equal(helpdeskErrorMessage(422, { detail: [{ msg: 'field required' }] }),
        'Please check the form: field required');
    assert.match(helpdeskErrorMessage(500, null), /HTTP 500/);
});
