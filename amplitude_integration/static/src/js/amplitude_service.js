/** @odoo-module **/

import { registry } from "@web/core/registry";
import { session } from "@web/session";
import { user } from "@web/core/user";

// Amplitude's unified browser loader. It is self-contained (Browser SDK +
// Autocapture + Session Replay) and is served per project API key. We build
// the URL dynamically from the configured key, so nothing is hard-coded.
function loaderUrl(apiKey) {
    return "https://cdn.amplitude.com/script/" + apiKey + ".js";
}

function loadScript(src) {
    return new Promise((resolve, reject) => {
        const el = document.createElement("script");
        el.type = "text/javascript";
        el.async = true;
        el.src = src;
        el.onload = () => resolve();
        el.onerror = () => reject(new Error("Failed to load " + src));
        document.head.appendChild(el);
    });
}

function identifyUser() {
    const amplitude = window.amplitude;
    if (!amplitude || !amplitude.Identify) {
        return;
    }
    // Use the same id the backend uses (login) so frontend + backend
    // events merge into one Amplitude user.
    const userId = user.login || ("odoo-" + user.userId);
    amplitude.setUserId(userId);

    const identify = new amplitude.Identify();
    identify.set("user_name", user.name || "");
    identify.set("login", user.login || "");
    identify.set("uid", user.userId || 0);
    identify.set("db_name", session.db || "");
    const companies =
        session.user_companies && session.user_companies.allowed_companies;
    const current =
        session.user_companies && session.user_companies.current_company;
    if (companies && current && companies[current]) {
        identify.set("company", companies[current].name);
    }
    amplitude.identify(identify);
}

async function initAmplitude(cfg) {
    try {
        await loadScript(loaderUrl(cfg.api_key));
        if (!window.amplitude || !window.amplitude.init) {
            throw new Error("amplitude global missing after load");
        }

        const options = {
            // Autocapture powers heatmaps, click/element tracking,
            // page views, sessions and form interactions.
            autocapture: {
                attribution: true,
                pageViews: true,
                sessions: true,
                formInteractions: true,
                fileDownloads: true,
                elementInteractions: !!cfg.enable_heatmap,
            },
            serverZone: cfg.server_zone || "US",
        };
        if (cfg.debug) {
            options.logLevel = 4; // Verbose
        }

        window.amplitude.init(cfg.api_key, options);
        identifyUser();

        // Session Replay ships inside the unified loader; it is switched on
        // for this source from the Amplitude dashboard (Settings -> the
        // browser SDK source -> Session Replay).
        if (cfg.debug) {
            console.info("[Amplitude] initialised via unified loader", {
                heatmap: cfg.enable_heatmap,
                session_replay: cfg.enable_session_replay,
                zone: cfg.server_zone,
            });
        }
    } catch (e) {
        console.warn("[Amplitude] initialisation failed:", e);
    }
}

export const amplitudeService = {
    start() {
        const cfg = session.amplitude;
        if (!cfg || !cfg.enabled || !cfg.api_key) {
            return {};
        }
        // Fire and forget; never block the web client.
        initAmplitude(cfg);
        return {};
    },
};

registry.category("services").add("amplitude_service", amplitudeService);
