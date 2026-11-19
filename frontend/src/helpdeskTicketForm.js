// Hidden marker the helpdesk agent appends when it wants the ticket form shown.
// Must match FORM_MARKER_OPEN/CLOSE in
// backend/domain/helpdesk/pipeline/ticket_form.py.
export const HELPDESK_FORM_OPEN = '[[HELPDESK_TICKET_FORM]]';
export const HELPDESK_FORM_CLOSE = '[[/HELPDESK_TICKET_FORM]]';

export const HELPDESK_PRIORITIES = ['Low', 'Medium', 'High'];

// Limits enforced by POST /api/v1/helpdesk_dev/incidents.
export const HELPDESK_LIMITS = { description: 255, incident_type: 100, incident_sub_type: 100 };

/**
 * Split a bot message into visible text and the ticket-form request.
 *
 * Returns { text, form } where form is null when there is no marker, or
 * { description } (the pre-fill) when there is one. A marker that is still
 * streaming in is hidden from the text but doesn't show the form yet.
 */
export function parseHelpdeskFormPayload(text = '') {
    const openIndex = text.indexOf(HELPDESK_FORM_OPEN);
    if (openIndex === -1) return { text, form: null };

    const closeIndex = text.indexOf(HELPDESK_FORM_CLOSE, openIndex + HELPDESK_FORM_OPEN.length);
    if (closeIndex === -1) {
        return { text: text.slice(0, openIndex).trimEnd(), form: null };
    }

    const before = text.slice(0, openIndex);
    const after = text.slice(closeIndex + HELPDESK_FORM_CLOSE.length);
    const jsonText = text.slice(openIndex + HELPDESK_FORM_OPEN.length, closeIndex);

    let description = '';
    try {
        const payload = JSON.parse(jsonText);
        if (typeof payload?.description === 'string') {
            description = payload.description.slice(0, HELPDESK_LIMITS.description);
        }
    } catch {
        // Bad pre-fill is not fatal — still show an empty form.
    }
    return { text: `${before}${after}`.trimEnd(), form: { description } };
}

/** Client-side check mirroring the backend; returns an error string or ''. */
export function validateHelpdeskForm(values) {
    const description = (values.description || '').trim();
    const type = (values.incident_type || '').trim();
    const subType = (values.incident_sub_type || '').trim();
    if (!description || !type || !subType) return 'Please fill in all required fields.';
    if (description.length > HELPDESK_LIMITS.description) {
        return `Description must be ${HELPDESK_LIMITS.description} characters or fewer.`;
    }
    if (type.length > HELPDESK_LIMITS.incident_type || subType.length > HELPDESK_LIMITS.incident_sub_type) {
        return 'Incident type and sub-type must be 100 characters or fewer.';
    }
    if (!HELPDESK_PRIORITIES.includes(values.priority)) return 'Please choose a priority.';
    return '';
}

/** Turn a FastAPI error body into one readable sentence. */
export function helpdeskErrorMessage(status, body) {
    const detail = body?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (Array.isArray(detail) && detail.length > 0) {
        return 'Please check the form: ' + detail
            .map((item) => item?.msg)
            .filter(Boolean)
            .join(', ');
    }
    return `Could not create the ticket (HTTP ${status}). Please try again.`;
}
