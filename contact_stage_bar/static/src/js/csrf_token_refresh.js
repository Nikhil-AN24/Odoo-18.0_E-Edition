/**
 * Retry a file download once when the server rejects a stale CSRF token.
 *
 * odoo.csrf_token is frozen into the page when it is served, and it is an
 * HMAC over the session id. Logging in rotates that id, so every tab that
 * was already open keeps posting a token the server no longer accepts and
 * answers with 400 "Session expired (invalid CSRF token)". The session is
 * perfectly alive - only the token the page is holding is out of date,
 * which is why reloading the tab has been the only way to print from it.
 *
 * Everything else in the client keeps working throughout, because JSON-RPC
 * is not CSRF-checked; /report/download is a type='http' POST, so printing
 * is where it shows.
 *
 * download._download is the seam core exposes for replacing this call (it
 * exists so tests can stub it), so nothing in core is patched here.
 */
import { download } from "@web/core/network/download";
import { rpc } from "@web/core/network/rpc";

const superDownload = download._download;

/**
 * The 400 arrives as HTML, so core cannot parse a real error out of it and
 * builds an "Arbitrary Uncaught Python Exception" whose debug text carries
 * the status and the body. Match on the server's wording rather than on
 * the status alone: a 400 from the report itself must still be reported.
 */
function isStaleCsrfError(error) {
    const debug = error && error.data && error.data.debug;
    return typeof debug === "string" && debug.includes("invalid CSRF token");
}

download._download = async (options) => {
    try {
        return await superDownload(options);
    } catch (error) {
        if (!isStaleCsrfError(error)) {
            throw error;
        }

        let fresh;
        try {
            fresh = (await rpc("/lgd/session/csrf_token")).csrf_token;
        } catch {
            // Session really is gone, or the server is unreachable. Report
            // the original download failure, not this one.
            throw error;
        }

        // Nothing to gain from retrying with the token we already sent -
        // that would turn one failed print into two.
        if (!fresh || fresh === odoo.csrf_token) {
            throw error;
        }

        odoo.csrf_token = fresh;
        // A download driven by a <form> carries its own hidden input, which
        // is what FormData(form) reads; odoo.csrf_token is not consulted.
        if (options.form) {
            const input = options.form.querySelector("input[name='csrf_token']");
            if (input) {
                input.value = fresh;
            }
        }
        return await superDownload(options);
    }
};
