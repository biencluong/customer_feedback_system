(() => {
  const $ = (id) => document.getElementById(id);

  let samples = [];
  let uploadedPayload = null;
  let lastResult = null;
  let activeTab = "samples";

  async function init() {
    bindTabs();
    bindResultTabs();
    bindUpload();
    bindClassificationRerun();
    $("run-btn").addEventListener("click", onRun);
    $("sample-select").addEventListener("change", renderSamplePreview);
    await Promise.all([loadHealth(), loadSamples()]);
  }

  async function loadHealth() {
    try {
      const h = await fetchJson("/api/health");
      const ready = h.llm_configured ?? h.azure_configured;
      $("health-dot").className = "dot " + (ready ? "ok" : "warn");
      if (ready) {
        const provider = h.provider === "openai" ? "OpenAI" : h.provider === "azure" ? "Azure OpenAI" : "LLM";
        $("health-text").textContent = `${provider} ready · ${h.model}`;
      } else {
        $("health-text").textContent = `No LLM creds · fallbacks only · ${h.model}`;
      }
    } catch (e) {
      $("health-dot").className = "dot warn";
      $("health-text").textContent = "API unreachable";
    }
  }

  async function loadSamples() {
    samples = await fetchJson("/api/samples");
    const sel = $("sample-select");
    sel.innerHTML = "";
    if (!samples.length) {
      sel.innerHTML = '<option value="">No samples found</option>';
      $("sample-preview").textContent = "Add JSON files under samples/inputs/";
      return;
    }
    for (const s of samples) {
      const opt = document.createElement("option");
      opt.value = s.name;
      opt.textContent = s.description;
      sel.appendChild(opt);
    }
    renderSamplePreview();
  }

  function renderSamplePreview() {
    const s = samples.find((x) => x.name === $("sample-select").value);
    $("sample-preview").textContent = s ? JSON.stringify(s.preview, null, 2) : "";
  }

  function bindTabs() {
    document.querySelectorAll(".tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        activeTab = btn.dataset.tab;
        document.querySelectorAll(".tab").forEach((b) => {
          b.classList.toggle("active", b === btn);
          b.setAttribute("aria-selected", b === btn ? "true" : "false");
        });
        document.querySelectorAll(".tab-panel").forEach((p) => {
          p.classList.toggle("active", p.id === `panel-${activeTab}`);
        });
      });
    });
  }

  function showResultView(which) {
    document.querySelectorAll(".result-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.result === which);
    });
    document.querySelectorAll(".result-view").forEach((el) => {
      el.classList.toggle("active", el.dataset.resultView === which);
    });
  }

  function bindResultTabs() {
    const tabs = document.querySelector(".result-tabs");
    if (!tabs) return;
    tabs.addEventListener("click", (e) => {
      const btn = e.target.closest(".result-tab");
      if (!btn) return;
      showResultView(btn.dataset.result);
    });
  }

  function bindUpload() {
    const zone = $("dropzone");
    const input = $("file-input");
    zone.addEventListener("click", () => input.click());
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.classList.add("dragover");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
    zone.addEventListener("drop", async (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
      if (e.dataTransfer.files[0]) await readUpload(e.dataTransfer.files[0]);
    });
    input.addEventListener("change", async () => {
      if (input.files[0]) await readUpload(input.files[0]);
    });
  }

  async function readUpload(file) {
    $("file-name").textContent = file.name;
    const text = await file.text();
    try {
      uploadedPayload = JSON.parse(text);
      $("upload-preview").hidden = false;
      $("upload-preview").textContent = JSON.stringify(uploadedPayload, null, 2);
      clearError();
    } catch (e) {
      uploadedPayload = null;
      $("upload-preview").hidden = true;
      showError(`Invalid JSON: ${e.message}`);
    }
  }

  async function onRun() {
    clearError();
    setLoading(true);
    try {
      let result;
      const simulate = $("simulate").value || null;
      if (activeTab === "samples") {
        const name = $("sample-select").value;
        if (!name) throw new Error("Select a sample first.");
        result = await fetchJson("/api/run/sample", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, simulate }),
        });
      } else if (activeTab === "upload") {
        const file = $("file-input").files[0];
        if (!file && !uploadedPayload) throw new Error("Choose a JSON file to upload.");
        if (file) {
          const fd = new FormData();
          fd.append("file", file);
          if (simulate) fd.append("simulate", simulate);
          result = await fetchJson("/api/run/upload", { method: "POST", body: fd });
        } else {
          result = await fetchJson("/api/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...uploadedPayload, simulate }),
          });
        }
      } else {
        const text = $("text").value.trim();
        if (!text) throw new Error("Enter feedback text.");
        result = await fetchJson("/api/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text,
            customer_id: $("customer-id").value.trim() || null,
            customer_email: $("customer-email").value.trim() || null,
            channel: $("channel").value,
            simulate,
          }),
        });
      }
      lastResult = result;
      renderResult(result);
    } catch (e) {
      showError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  const CATEGORIES = [
    "bug_report",
    "billing_issue",
    "feature_request",
    "account_access",
    "churn_risk",
    "abuse",
    "praise",
    "other",
  ];
  const URGENCIES = ["low", "medium", "high", "critical"];
  const SENTIMENTS = ["positive", "neutral", "negative"];

  function optionList(values, selected, includeBlank) {
    const opts = [];
    if (includeBlank) {
      opts.push(`<option value=""${!selected ? " selected" : ""}>— none —</option>`);
    }
    for (const v of values) {
      opts.push(`<option value="${v}"${v === selected ? " selected" : ""}>${v}</option>`);
    }
    return opts.join("");
  }

  function bindClassificationRerun() {
    $("view-report").addEventListener("click", (e) => {
      const btn = e.target.closest("#rerun-classification-btn");
      if (!btn || btn.disabled) return;
      onRerunWithClassification();
    });
  }

  function readClassificationForm() {
    const category = $("edit-category").value;
    const secondary = $("edit-secondary").value || null;
    const urgency = $("edit-urgency").value;
    const sentiment = $("edit-sentiment").value;
    let rationale = $("edit-rationale").value.trim();
    if (!category) throw new Error("Category is required.");
    if (!rationale) {
      rationale = `Operator override: classified as ${category}.`;
    } else if (!/operator override/i.test(rationale)) {
      rationale = `Operator override. ${rationale}`;
    }
    return {
      category,
      secondary_category: secondary,
      urgency,
      sentiment,
      confidence: 1.0,
      rationale,
    };
  }

  async function onRerunWithClassification() {
    if (!lastResult || !lastResult.report) {
      showError("Run triage once before adjusting classification.");
      return;
    }
    clearError();
    const btn = $("rerun-classification-btn");
    const runBtn = $("run-btn");
    try {
      const classification = readClassificationForm();
      const fb = lastResult.report.feedback;
      if (btn) btn.disabled = true;
      setLoading(true);
      const result = await fetchJson("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: fb.text,
          customer_id: fb.customer_id || null,
          customer_email: fb.customer_email || null,
          channel: fb.channel || "web_form",
          feedback_id: fb.feedback_id,
          simulate: $("simulate").value || null,
          classification,
        }),
      });
      lastResult = result;
      renderResult(result);
    } catch (e) {
      showError(e.message || String(e));
      if (btn) btn.disabled = false;
    } finally {
      setLoading(false);
      runBtn.disabled = false;
    }
  }

  function renderResult(result) {
    const r = result.report;
    $("empty-state").hidden = true;
    $("result").hidden = false;
    $("result-id").textContent = r.feedback.feedback_id;
    $("result-title").textContent = "Triage report";

    const badges = $("badges");
    badges.innerHTML = "";
    badges.appendChild(badge(`confidence: ${r.confidence}`, r.confidence));
    badges.appendChild(badge(r.classification.category, "ok"));
    badges.appendChild(badge(`urgency: ${r.classification.urgency}`, r.classification.urgency === "critical" || r.classification.urgency === "high" ? "danger" : "ok"));
    if (r.needs_human_review) badges.appendChild(badge("needs review", "review"));
    if (r.degraded) badges.appendChild(badge("degraded", "warn"));

    $("view-report").innerHTML = buildReportHtml(r);
    $("view-trace").innerHTML = (result.trace || [])
      .map((ev) => {
        const { t, kind, ...rest } = ev;
        return `<div class="trace-row">
          <div class="trace-t">${Number(t).toFixed(2)}s</div>
          <div>
            <div class="trace-kind">${escapeHtml(kind)}</div>
            <div class="trace-body">${escapeHtml(JSON.stringify(rest))}</div>
          </div>
        </div>`;
      })
      .join("") || "<p class='lede'>No trace events.</p>";
    $("view-json").textContent = JSON.stringify(result, null, 2);

    showResultView("report");
  }

  function buildReportHtml(r) {
    const c = r.classification;
    const refs = (r.references || [])
      .map((ref) => `<li><code>${escapeHtml(ref.source_id)}</code> ${escapeHtml(ref.title)} — ${escapeHtml(ref.relevance)}</li>`)
      .join("") || "<li>none</li>";
    const actions = (r.suggested_actions || [])
      .map(
        (a) =>
          `<li>${escapeHtml(a.action)} <em>(${escapeHtml(a.owner)})</em>` +
          (a.basis ? ` · basis <code>${escapeHtml(a.basis)}</code>` : "") +
          (a.requires_approval ? " · <strong>requires approval</strong>" : "") +
          `</li>`
      )
      .join("") || "<li>none</li>";
    const flags = (r.flags || [])
      .map((f) => `<div class="flag"><code>${escapeHtml(f.code)}</code><span>${escapeHtml(f.detail)}</span></div>`)
      .join("") || "<p>none</p>";

    return `
      <div class="block">
        <h3>Input</h3>
        <blockquote class="quote">${escapeHtml(r.feedback.text)}</blockquote>
      </div>
      <div class="block">
        <h3>Classification</h3>
        <p class="edit-hint">Adjust fields below and rerun to regenerate context + report with your classification (classifier is skipped).</p>
        <div class="class-edit">
          <label>
            <span>Category</span>
            <select id="edit-category">${optionList(CATEGORIES, c.category, false)}</select>
          </label>
          <label>
            <span>Secondary</span>
            <select id="edit-secondary">${optionList(CATEGORIES, c.secondary_category || "", true)}</select>
          </label>
          <label>
            <span>Urgency</span>
            <select id="edit-urgency">${optionList(URGENCIES, c.urgency, false)}</select>
          </label>
          <label>
            <span>Sentiment</span>
            <select id="edit-sentiment">${optionList(SENTIMENTS, c.sentiment, false)}</select>
          </label>
        </div>
        <label class="field-label" for="edit-rationale">Rationale</label>
        <textarea id="edit-rationale" rows="2">${escapeHtml(c.rationale)}</textarea>
        <div class="confidence-callout" data-tone="${confidenceTone(c.confidence)}">
          <span class="confidence-callout-label">Previous classifier confidence</span>
          <strong class="confidence-callout-value">${Number(c.confidence).toFixed(2)}</strong>
          <span class="confidence-callout-note">Override reruns use confidence 1.0</span>
        </div>
        <button type="button" class="rerun-btn" id="rerun-classification-btn">Rerun triage with this classification</button>
      </div>
      <div class="block">
        <h3>Summary</h3>
        <p>${escapeHtml(r.summary)}</p>
      </div>
      <div class="block">
        <h3>Customer context${r.customer_found ? "" : " (no record)"}</h3>
        <p>${escapeHtml(r.customer_context)}</p>
      </div>
      <div class="block">
        <h3>Policy / guideline references</h3>
        <ul>${refs}</ul>
      </div>
      <div class="block">
        <h3>Suggested next actions</h3>
        <ul>${actions}</ul>
      </div>
      <div class="block">
        <h3>Flags</h3>
        ${flags}
      </div>
    `;
  }

  function confidenceTone(score) {
    const n = Number(score);
    if (n >= 0.8) return "high";
    if (n >= 0.6) return "medium";
    return "low";
  }

  function badge(text, tone) {
    const el = document.createElement("span");
    el.className = `badge ${tone || ""}`;
    el.textContent = text;
    return el;
  }

  function setLoading(on) {
    $("run-btn").disabled = on;
    $("run-btn").querySelector(".run-label").hidden = on;
    $("run-btn").querySelector(".run-spinner").hidden = !on;
  }

  function showError(msg) {
    const el = $("error");
    el.hidden = false;
    el.textContent = msg;
  }

  function clearError() {
    $("error").hidden = true;
    $("error").textContent = "";
  }

  async function fetchJson(url, opts) {
    const res = await fetch(url, opts);
    let body;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    if (!res.ok) {
      const detail = body && (body.detail || body.message);
      throw new Error(typeof detail === "string" ? detail : `Request failed (${res.status})`);
    }
    return body;
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  init();
})();
