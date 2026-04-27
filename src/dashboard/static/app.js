/* eslint-disable no-undef */

async function refreshState() {
    try {
        const res = await fetch("/api/state");
        if (!res.ok) return;
        const data = await res.json();
        renderState(data);
    } catch (err) {
        console.warn("state refresh failed", err);
    }
}

function renderState(data) {
    const target = data.target || 50;
    const completed =
        (data.counts.applied || 0) + (data.counts.exhausted || 0);
    const pct = Math.min(100, Math.round((completed / target) * 100));
    document.getElementById("progress-bar").style.width = pct + "%";
    document.getElementById("progress-label").innerText =
        `${completed} / ${target} Unternehmen abgeschlossen`;

    const apps = data.applications || {};
    document.getElementById("m-applied").innerText = apps.applied || 0;
    document.getElementById("m-confirmed").innerText = apps.confirmed || 0;
    document.getElementById("m-interview").innerText = apps.interview_invitation || 0;
    document.getElementById("m-rejected").innerText = apps.rejected || 0;
    document.getElementById("m-offer").innerText = apps.offer || 0;
    document.getElementById("m-failed").innerText = apps.failed || 0;

    const grid = document.getElementById("companies");
    grid.innerHTML = "";
    for (const c of data.companies) {
        const el = document.createElement("div");
        el.className = "company";

        const jobs = (c.jobs || [])
            .map(
                (j) => {
                    const apps = (j.applications || []).map(
                        (a) => `<span class="app-status-${a.status}">${a.status}</span>`
                    ).join(" · ");
                    return `<li>${escapeHtml(j.title)} ${apps ? "— " + apps : ""}</li>`;
                }
            )
            .join("");
        el.innerHTML = `
            <div class="name">${escapeHtml(c.name)}<span class="status-pill status-${c.status}">${c.status}</span></div>
            <div class="meta">${escapeHtml(c.city || "—")} · ${escapeHtml(c.domain || "")}</div>
            ${jobs ? `<ul class="jobs">${jobs}</ul>` : ""}
        `;
        grid.appendChild(el);
    }
}

function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    }[c]));
}

function startEventStream() {
    const list = document.getElementById("events");
    const es = new EventSource("/api/events");
    es.addEventListener("update", (msg) => {
        let payload;
        try {
            payload = JSON.parse(msg.data);
        } catch {
            return;
        }
        const li = document.createElement("li");
        const time = new Date().toLocaleTimeString();
        const agent = payload.agent || "system";
        const event = payload.event || "";
        const tail = JSON.stringify(payload).slice(0, 300);
        li.innerHTML = `<span class="time">${time}</span><span class="agent">[${agent}]</span> ${escapeHtml(event)} — ${escapeHtml(tail)}`;
        list.prepend(li);
        while (list.children.length > 200) list.removeChild(list.lastChild);
        // refresh aggregates on important events
        if (
            event === "application_submitted" ||
            event === "company_done" ||
            event === "status_updated" ||
            event === "sourcing_added"
        ) {
            refreshState();
        }
    });
    es.onerror = () => {
        // browser auto-reconnects
    };
}

refreshState();
setInterval(refreshState, 15_000);
startEventStream();
