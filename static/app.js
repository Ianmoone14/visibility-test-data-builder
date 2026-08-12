(() => {
  "use strict";

  const state = {
    elements: [],
    templates: [],
    scenarios: [],
    activeScenario: null,
    export: { python: "expected_visibility = {}", json: "{}" },
    keyManual: false,
    view: "buttons",
    groupFilter: "all",
    collapsedGroups: {},
    editingTemplateId: null,
    buttonPickerQuery: "",
    templatePickerQuery: "",
    templateSelectedIds: [],
    buttonModalQuery: "",
  };

  const $ = (id) => document.getElementById(id);

  function toast(msg, isError = false) {
    const el = $("toast");
    el.textContent = msg;
    el.hidden = false;
    el.classList.toggle("error", isError);
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (el.hidden = true), 2500);
  }

  function setError(id, msg) {
    const el = $(id);
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = msg;
  }

  function esc(v) {
    return String(v ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function snake(v) {
    return v
      .trim()
      .toLowerCase()
      .replace(/['"]/g, "")
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .replace(/_+/g, "_");
  }

  function viewFromHash() {
    const hash = (location.hash || "").replace(/^#/, "");
    if (["buttons", "templates", "scenarios"].includes(hash)) return hash;
    return "buttons";
  }

  function showView(view, options = {}) {
    const resetScenario = options.resetScenario !== false;
    const skipHistory = options.skipHistory === true;
    if (!["buttons", "templates", "scenarios"].includes(view)) view = "buttons";

    state.view = view;
    ["buttons", "templates", "scenarios"].forEach((name) => {
      const el = $(`view-${name}`);
      if (el) el.hidden = name !== view;
    });
    document.querySelectorAll(".nav-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.view === view);
    });
    if (view === "templates") renderPicker();
    if (view === "scenarios") {
      if (resetScenario) resetScenarioScreen();
      else {
        renderWorkspace();
        renderFancyPickers();
      }
    }

    if (!skipHistory) {
      const target = `#${view}`;
      if (location.hash !== target) {
        history.pushState({ view }, "", target);
      } else {
        history.replaceState({ view }, "", target);
      }
    }
  }

  function resetScenarioScreen() {
    state.activeScenario = null;
    const form = $("scenario-form");
    form.reset();
    form.hidden = true;
    setError("scenario-error", "");
    $("scenario-select").value = "";
    closePickers();
    renderScenarioSelect();
    renderWorkspace();
    clearExport();
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const text = await res.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = { detail: text };
      }
    }
    if (!res.ok) {
      const d = data?.detail;
      let message = "Request failed";
      if (typeof d === "string") message = d;
      else if (Array.isArray(d)) {
        message = d.map((x) => x.msg || JSON.stringify(x)).join("; ");
      }
      throw new Error(message);
    }
    return data;
  }

  function groupNameOf(el) {
    const g = (el.group_name || "").trim();
    return g || "Ungrouped";
  }

  function groupElements(list) {
    const map = new Map();
    for (const el of list) {
      const g = groupNameOf(el);
      if (!map.has(g)) map.set(g, []);
      map.get(g).push(el);
    }
    return [...map.entries()].sort((a, b) => {
      if (a[0] === "Ungrouped") return 1;
      if (b[0] === "Ungrouped") return -1;
      return a[0].localeCompare(b[0]);
    });
  }

  function allGroupNames() {
    return [
      ...new Set(state.elements.map((el) => groupNameOf(el))),
    ].sort((a, b) => {
      if (a === "Ungrouped") return 1;
      if (b === "Ungrouped") return -1;
      return a.localeCompare(b);
    });
  }

  async function loadElements(search = "") {
    const q = search ? `?search=${encodeURIComponent(search)}` : "";
    state.elements = await api(`/api/elements${q}`);
    const groups = allGroupNames();
    if (state.groupFilter !== "all" && !groups.includes(state.groupFilter)) {
      state.groupFilter = "all";
    }
    renderElements();
    renderPicker();
    renderFancyPickers();
  }

  async function loadTemplates() {
    state.templates = await api("/api/templates");
    renderTemplates();
    renderFancyPickers();
  }

  async function loadScenarios() {
    state.scenarios = await api("/api/scenarios");
    renderScenarioSelect();
  }

  function isDraft() {
    return !!(state.activeScenario && state.activeScenario.isDraft);
  }

  function startDraft(template = null) {
    const elements = [];
    if (template?.elements?.length) {
      for (const el of template.elements) {
        elements.push({
          element_id: el.id,
          display_name: el.display_name,
          technical_key: el.technical_key,
          group_name: el.group_name || "",
          is_visible: true,
        });
      }
    }
    state.activeScenario = {
      id: null,
      isDraft: true,
      name: "Draft",
      description: "Not saved — use Save scenario when ready",
      elements,
    };
    $("scenario-select").value = "";
    $("scenario-form").hidden = true;
    showView("scenarios", { resetScenario: false });
    renderWorkspace();
    refreshExport();
  }

  async function openScenario(id) {
    if (!id) {
      state.activeScenario = null;
      renderWorkspace();
      clearExport();
      return;
    }
    state.activeScenario = await api(`/api/scenarios/${id}`);
    state.activeScenario.isDraft = false;
    $("scenario-select").value = String(id);
    $("scenario-form").hidden = true;
    renderWorkspace();
    await refreshExport();
  }

  function buildLocalExport(elements) {
    const mapping = {};
    [...elements]
      .sort((a, b) => a.technical_key.localeCompare(b.technical_key))
      .forEach((el) => {
        mapping[el.technical_key] = !!el.is_visible;
      });
    const keys = Object.keys(mapping);
    let python = "expected_visibility = {}";
    if (keys.length) {
      python =
        "expected_visibility = {\n" +
        keys.map((k) => `    "${k}": ${mapping[k] ? "True" : "False"},`).join("\n") +
        "\n}";
    }
    return {
      mapping,
      python,
      json: JSON.stringify(mapping, null, 2),
    };
  }

  async function refreshExport() {
    if (!state.activeScenario) return clearExport();
    const data = isDraft()
      ? buildLocalExport(state.activeScenario.elements || [])
      : await api(`/api/scenarios/${state.activeScenario.id}/export`);
    state.export = data;
    $("python-preview").textContent = data.python;
    $("json-preview").textContent = data.json;
    $("copy-python-btn").disabled = false;
    $("copy-json-btn").disabled = false;
  }

  function clearExport() {
    state.export = { python: "expected_visibility = {}", json: "{}" };
    $("python-preview").textContent = state.export.python;
    $("json-preview").textContent = state.export.json;
    $("copy-python-btn").disabled = true;
    $("copy-json-btn").disabled = true;
  }

  function renderGroupSuggestions() {
    const list = $("group-suggestions");
    if (!list) return;
    list.innerHTML = allGroupNames()
      .filter((g) => g !== "Ungrouped")
      .map((g) => `<option value="${esc(g)}"></option>`)
      .join("");
  }

  function renderGroupFilters() {
    const root = $("group-filters");
    if (!root) return;
    const groups = allGroupNames();
    if (!groups.length) {
      root.innerHTML = "";
      return;
    }
    root.innerHTML = [
      `<button type="button" class="chip ${
        state.groupFilter === "all" ? "active" : ""
      }" data-group="all">All (${state.elements.length})</button>`,
      ...groups.map((g) => {
        const count = state.elements.filter((el) => groupNameOf(el) === g).length;
        return `<button type="button" class="chip ${
          state.groupFilter === g ? "active" : ""
        }" data-group="${esc(g)}">${esc(g)} (${count})</button>`;
      }),
    ].join("");
  }

  function renderElements() {
    const root = $("elements-list");
    const countEl = $("elements-count");
    renderGroupFilters();
    renderGroupSuggestions();

    let list = state.elements;
    if (state.groupFilter !== "all") {
      list = list.filter((el) => groupNameOf(el) === state.groupFilter);
    }
    if (countEl) countEl.textContent = list.length ? `(${list.length})` : "";

    if (!list.length) {
      root.innerHTML = `<div class="empty">No buttons yet</div>`;
      return;
    }

    root.innerHTML = groupElements(list)
      .map(([group, items]) => {
        const collapsed = !!state.collapsedGroups[group];
        return `
        <div class="group-block ${collapsed ? "collapsed" : ""}" data-group="${esc(group)}">
          <button type="button" class="group-head" data-act="toggle-group">
            <span class="chevron">${collapsed ? "▸" : "▾"}</span>
            <span class="group-title">${esc(group)}</span>
            <span class="group-count">${items.length}</span>
          </button>
          <div class="group-body">
            ${items
              .map(
                (el) => `
              <div class="item" data-id="${el.id}">
                <div>
                  <p class="name">${esc(el.display_name)}</p>
                  <p class="key">${esc(el.technical_key)}</p>
                </div>
                <div class="actions">
                  <button type="button" class="btn btn-sm danger" data-act="del">Delete</button>
                </div>
              </div>`
              )
              .join("")}
          </div>
        </div>`;
      })
      .join("");
  }

  function syncHiddenPicker() {
    const root = $("template-element-picker");
    if (!root) return;
    root.innerHTML = state.templateSelectedIds
      .map(
        (id) =>
          `<input type="checkbox" name="element_ids" value="${id}" checked />`
      )
      .join("");
  }

  function renderSelectedButtonsPreview() {
    const label = $("selected-buttons-label");
    const preview = $("selected-buttons-preview");
    const count = state.templateSelectedIds.length;
    if (label) {
      label.textContent = count
        ? `${count} button${count === 1 ? "" : "s"} selected`
        : "Select buttons…";
    }
    if (!preview) return;
    if (!count) {
      preview.innerHTML = "";
      return;
    }
    const selected = state.elements.filter((el) =>
      state.templateSelectedIds.includes(el.id)
    );
    preview.innerHTML = selected
      .map(
        (el) =>
          `<span class="tag">${esc(el.display_name)} <button type="button" data-remove-id="${el.id}" aria-label="Remove">×</button></span>`
      )
      .join("");
  }

  function renderPicker(selectedIds = null) {
    if (selectedIds) {
      state.templateSelectedIds = [...selectedIds];
    }
    syncHiddenPicker();
    renderSelectedButtonsPreview();
  }

  function openButtonModal() {
    state.buttonModalQuery = "";
    $("button-modal-search").value = "";
    $("button-modal").hidden = false;
    renderButtonModal();
    $("button-modal-search").focus();
  }

  function closeButtonModal() {
    $("button-modal").hidden = true;
    renderPicker();
  }

  function renderButtonModal() {
    const root = $("button-modal-list");
    const countEl = $("button-modal-count");
    const selected = new Set(state.templateSelectedIds);
    countEl.textContent = `${selected.size} selected`;

    let list = state.elements;
    const q = state.buttonModalQuery.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (el) =>
          el.display_name.toLowerCase().includes(q) ||
          el.technical_key.toLowerCase().includes(q) ||
          groupNameOf(el).toLowerCase().includes(q)
      );
    }

    if (!list.length) {
      root.innerHTML = `<div class="empty">No matching buttons</div>`;
      return;
    }

    root.innerHTML = groupElements(list)
      .map(([group, items]) => {
        const allOn = items.every((el) => selected.has(el.id));
        return `
        <div class="modal-group">
          <div class="modal-group-head">
            <span>${esc(group)}</span>
            <button type="button" class="btn btn-sm" data-toggle-group="${esc(group)}" data-on="${
              allOn ? "0" : "1"
            }">${allOn ? "Clear group" : "Select group"}</button>
          </div>
          <div class="modal-group-body">
            ${items
              .map(
                (el) => `
              <label class="modal-option ${selected.has(el.id) ? "checked" : ""}">
                <input type="checkbox" value="${el.id}" ${
                  selected.has(el.id) ? "checked" : ""
                } />
                <span>
                  <strong>${esc(el.display_name)}</strong>
                  <code>${esc(el.technical_key)}</code>
                </span>
              </label>`
              )
              .join("")}
          </div>
        </div>`;
      })
      .join("");
  }

  function setTemplateFormMode(template = null) {
    const form = $("template-form");
    if (!template) {
      state.editingTemplateId = null;
      form.edit_id.value = "";
      form.reset();
      $("template-form-title").textContent = "New template";
      $("template-submit-btn").textContent = "Save template";
      $("template-cancel-edit").hidden = true;
      renderPicker([]);
      return;
    }
    state.editingTemplateId = template.id;
    form.edit_id.value = String(template.id);
    form.name.value = template.name;
    form.description.value = template.description || "";
    $("template-form-title").textContent = "Edit template";
    $("template-submit-btn").textContent = "Save changes";
    $("template-cancel-edit").hidden = false;
    renderPicker(template.element_ids || template.elements.map((e) => e.id));
  }

  function renderTemplates() {
    const root = $("templates-list");
    if (!state.templates.length) {
      root.innerHTML = `<div class="empty">No templates yet</div>`;
      return;
    }
    root.innerHTML = state.templates
      .map((t) => {
        const keys = t.elements.map((e) => e.technical_key).join(", ");
        return `
        <div class="item" data-id="${t.id}">
          <div>
            <p class="name">${esc(t.name)}</p>
            <p class="key">${esc(keys || "empty")}</p>
          </div>
          <div class="actions">
            <button type="button" class="btn btn-sm" data-act="edit">Edit</button>
            <button type="button" class="btn btn-sm btn-primary" data-act="use">Use in scenario</button>
            <button type="button" class="btn btn-sm danger" data-act="del">Delete</button>
          </div>
        </div>`;
      })
      .join("");
  }

  function renderScenarioSelect() {
    const sel = $("scenario-select");
    const cur =
      state.activeScenario && !state.activeScenario.isDraft
        ? String(state.activeScenario.id)
        : "";
    sel.innerHTML =
      `<option value="">Select saved scenario</option>` +
      state.scenarios
        .map(
          (s) =>
            `<option value="${s.id}" ${String(s.id) === cur ? "selected" : ""}>${esc(
              s.name
            )}</option>`
        )
        .join("");
  }

  function renderWorkspace() {
    const has = !!state.activeScenario;
    const draft = isDraft();
    $("scenario-empty").hidden = has;
    $("scenario-workspace").hidden = !has;
    $("save-scenario-btn").disabled = !has;
    $("delete-scenario-btn").disabled = !has;
    $("delete-scenario-btn").textContent = draft ? "Clear" : "Delete";
    $("draft-badge").hidden = !draft;
    if (!has) {
      clearExport();
      return;
    }
    $("active-scenario-name").textContent = draft
      ? "Quick start"
      : state.activeScenario.name;
    $("active-scenario-desc").textContent = draft
      ? "Temporary workspace — not saved until you click Save scenario"
      : state.activeScenario.description || "";
    renderScenarioElements();
    renderFancyPickers();
  }

  function renderScenarioElements() {
    const root = $("scenario-elements");
    const els = state.activeScenario?.elements || [];
    if (!els.length) {
      root.innerHTML = `<div class="empty">Add buttons or apply a template</div>`;
      return;
    }
    root.innerHTML = els
      .map((el) => {
        const yes = !!el.is_visible;
        return `
        <div class="scenario-row" data-id="${el.element_id}">
          <div>
            <p class="name">${esc(el.display_name)}</p>
            <p class="key">${esc(el.technical_key)}</p>
          </div>
          <button
            type="button"
            class="switch ${yes ? "is-on" : "is-off"}"
            data-act="toggle"
            aria-pressed="${yes ? "true" : "false"}"
            title="${yes ? "Visible — click to hide" : "Hidden — click to show"}"
          >
            <span class="switch-track"><span class="switch-knob"></span></span>
            <span class="switch-label">${yes ? "Visible" : "Hidden"}</span>
          </button>
          <button type="button" class="btn btn-sm danger" data-act="remove">Remove</button>
        </div>`;
      })
      .join("");
  }

  function closePickers() {
    $("button-picker-menu").hidden = true;
    $("template-picker-menu").hidden = true;
  }

  function renderFancyPickers() {
    renderButtonPickerOptions();
    renderTemplatePickerOptions();
  }

  function renderButtonPickerOptions() {
    const root = $("button-picker-options");
    if (!root) return;
    if (!state.activeScenario) {
      root.innerHTML = `<div class="picker-empty">Open a scenario first</div>`;
      return;
    }
    const used = new Set(
      (state.activeScenario.elements || []).map((e) => e.element_id)
    );
    const q = state.buttonPickerQuery.trim().toLowerCase();
    let available = state.elements.filter((e) => !used.has(e.id));
    if (q) {
      available = available.filter(
        (e) =>
          e.display_name.toLowerCase().includes(q) ||
          e.technical_key.toLowerCase().includes(q) ||
          groupNameOf(e).toLowerCase().includes(q)
      );
    }
    if (!available.length) {
      root.innerHTML = `<div class="picker-empty">No matching buttons</div>`;
      return;
    }
    root.innerHTML = groupElements(available)
      .map(
        ([group, items]) => `
      <div class="picker-section">
        <div class="picker-section-title">${esc(group)}</div>
        ${items
          .map(
            (e) => `
          <button type="button" class="picker-option" data-element-id="${e.id}">
            <span class="picker-option-name">${esc(e.display_name)}</span>
            <span class="picker-option-key">${esc(e.technical_key)}</span>
          </button>`
          )
          .join("")}
      </div>`
      )
      .join("");
  }

  function renderTemplatePickerOptions() {
    const root = $("template-picker-options");
    if (!root) return;
    const q = state.templatePickerQuery.trim().toLowerCase();
    let list = state.templates;
    if (q) {
      list = list.filter(
        (t) =>
          t.name.toLowerCase().includes(q) ||
          (t.description || "").toLowerCase().includes(q)
      );
    }
    if (!list.length) {
      root.innerHTML = `<div class="picker-empty">No templates</div>`;
      return;
    }
    root.innerHTML = list
      .map((t) => {
        const count = t.elements?.length || 0;
        return `
        <button type="button" class="picker-option" data-template-id="${t.id}">
          <span class="picker-option-name">${esc(t.name)}</span>
          <span class="picker-option-key">${count} button${count === 1 ? "" : "s"}</span>
        </button>`;
      })
      .join("");
  }

  async function addElementToScenario(elementId) {
    if (isDraft()) {
      if (state.activeScenario.elements.some((e) => e.element_id === elementId)) {
        toast("Already in draft", true);
        return;
      }
      const el = state.elements.find((e) => e.id === elementId);
      if (!el) throw new Error("Button not found");
      state.activeScenario.elements.push({
        element_id: el.id,
        display_name: el.display_name,
        technical_key: el.technical_key,
        group_name: el.group_name || "",
        is_visible: true,
      });
      renderWorkspace();
      refreshExport();
      toast("Button added");
      return;
    }
    state.activeScenario = await api(
      `/api/scenarios/${state.activeScenario.id}/elements`,
      {
        method: "POST",
        body: JSON.stringify({ element_id: elementId, is_visible: true }),
      }
    );
    renderWorkspace();
    await refreshExport();
    toast("Button added");
  }

  async function applyTemplateToActive(templateId) {
    if (isDraft()) {
      const template = state.templates.find((t) => t.id === templateId);
      if (!template) throw new Error("Template not found");
      const existing = new Set(
        state.activeScenario.elements.map((e) => e.element_id)
      );
      let added = 0;
      for (const el of template.elements) {
        if (existing.has(el.id)) continue;
        state.activeScenario.elements.push({
          element_id: el.id,
          display_name: el.display_name,
          technical_key: el.technical_key,
          group_name: el.group_name || "",
          is_visible: true,
        });
        added += 1;
      }
      renderWorkspace();
      refreshExport();
      toast(`Template applied · ${added} added`);
      return;
    }
    state.activeScenario = await api(
      `/api/scenarios/${state.activeScenario.id}/apply-template`,
      { method: "POST", body: JSON.stringify({ template_id: templateId }) }
    );
    const s = state.activeScenario.apply_summary || {};
    renderWorkspace();
    await refreshExport();
    toast(`Template applied · ${s.added || 0} added`);
  }

  function useTemplate(id) {
    const template = state.templates.find((t) => t.id === id);
    if (!template) {
      toast("Template not found", true);
      return;
    }
    startDraft(template);
    toast(`Draft ready · ${template.elements.length} buttons`);
  }

  async function copy(text, label) {
    try {
      await navigator.clipboard.writeText(text);
      toast(`${label} copied`);
    } catch {
      toast("Could not copy", true);
    }
  }

  // --- Events ---

  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => showView(btn.dataset.view));
  });

  window.addEventListener("popstate", (e) => {
    const view = e.state?.view || viewFromHash();
    showView(view, {
      skipHistory: true,
      resetScenario: view === "scenarios" && !state.activeScenario,
    });
  });

  const elementForm = $("element-form");
  elementForm.display_name.addEventListener("input", (e) => {
    if (!state.keyManual) elementForm.technical_key.value = snake(e.target.value);
  });
  elementForm.technical_key.addEventListener("input", () => {
    state.keyManual = true;
  });

  elementForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    setError("element-error", "");
    try {
      await api("/api/elements", {
        method: "POST",
        body: JSON.stringify({
          display_name: elementForm.display_name.value,
          technical_key: elementForm.technical_key.value,
          group_name: elementForm.group_name.value,
        }),
      });
      elementForm.reset();
      state.keyManual = false;
      await loadElements($("element-search").value.trim());
      toast("Button added");
    } catch (err) {
      setError("element-error", err.message);
    }
  });

  let searchT;
  $("element-search").addEventListener("input", (e) => {
    clearTimeout(searchT);
    searchT = setTimeout(
      () =>
        loadElements(e.target.value.trim()).catch((err) =>
          toast(err.message, true)
        ),
      180
    );
  });

  $("elements-list").addEventListener("click", async (e) => {
    const toggle = e.target.closest('[data-act="toggle-group"]');
    if (toggle) {
      const block = toggle.closest(".group-block");
      const group = block.dataset.group;
      state.collapsedGroups[group] = !state.collapsedGroups[group];
      renderElements();
      return;
    }
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const item = btn.closest(".item");
    if (!item) return;
    const id = Number(item.dataset.id);
    try {
      if (btn.dataset.act === "del") {
        if (!confirm("Delete this button?")) return;
        await api(`/api/elements/${id}`, { method: "DELETE" });
        await Promise.all([
          loadElements($("element-search").value.trim()),
          loadTemplates(),
        ]);
        if (state.activeScenario) await openScenario(state.activeScenario.id);
        toast("Deleted");
      }
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("group-filters").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    state.groupFilter = chip.dataset.group;
    renderElements();
  });

  $("quick-scenario-btn").addEventListener("click", () => {
    startDraft();
    toast("Quick start — not saved");
  });

  $("save-scenario-btn").addEventListener("click", () => {
    if (!state.activeScenario) return;
    $("scenario-form").hidden = false;
    if (!isDraft()) {
      $("scenario-form").name.value = state.activeScenario.name || "";
      $("scenario-form").description.value =
        state.activeScenario.description || "";
    } else {
      $("scenario-form").name.value = "";
      $("scenario-form").description.value = "";
    }
    $("scenario-form").name.focus();
  });

  $("cancel-scenario-btn").addEventListener("click", () => {
    $("scenario-form").hidden = true;
    setError("scenario-error", "");
  });

  $("scenario-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setError("scenario-error", "");
    const form = e.target;
    const name = form.name.value.trim();
    const description = form.description.value.trim();
    if (!name) {
      setError("scenario-error", "Scenario name is required");
      return;
    }
    try {
      if (isDraft()) {
        const draftElements = [...(state.activeScenario.elements || [])];
        const created = await api("/api/scenarios", {
          method: "POST",
          body: JSON.stringify({ name, description }),
        });
        for (const el of draftElements) {
          await api(`/api/scenarios/${created.id}/elements`, {
            method: "POST",
            body: JSON.stringify({
              element_id: el.element_id,
              is_visible: !!el.is_visible,
            }),
          });
        }
        await loadScenarios();
        form.reset();
        form.hidden = true;
        await openScenario(created.id);
        toast("Scenario saved");
        return;
      }

      await api(`/api/scenarios/${state.activeScenario.id}`, {
        method: "PUT",
        body: JSON.stringify({ name, description }),
      });
      await loadScenarios();
      form.hidden = true;
      await openScenario(state.activeScenario.id);
      toast("Scenario updated");
    } catch (err) {
      setError("scenario-error", err.message);
    }
  });

  $("scenario-select").addEventListener("change", async (e) => {
    try {
      await openScenario(e.target.value ? Number(e.target.value) : null);
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("delete-scenario-btn").addEventListener("click", async () => {
    if (!state.activeScenario) return;
    if (isDraft()) {
      state.activeScenario = null;
      $("scenario-form").hidden = true;
      renderWorkspace();
      clearExport();
      toast("Draft cleared");
      return;
    }
    if (!confirm(`Delete “${state.activeScenario.name}”?`)) return;
    try {
      await api(`/api/scenarios/${state.activeScenario.id}`, {
        method: "DELETE",
      });
      state.activeScenario = null;
      await loadScenarios();
      renderWorkspace();
      clearExport();
      toast("Scenario deleted");
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("button-picker-trigger").addEventListener("click", (e) => {
    e.stopPropagation();
    const menu = $("button-picker-menu");
    const open = menu.hidden;
    closePickers();
    if (open) {
      menu.hidden = false;
      state.buttonPickerQuery = "";
      $("button-picker-search").value = "";
      renderButtonPickerOptions();
      $("button-picker-search").focus();
    }
  });

  $("template-picker-trigger").addEventListener("click", (e) => {
    e.stopPropagation();
    const menu = $("template-picker-menu");
    const open = menu.hidden;
    closePickers();
    if (open) {
      menu.hidden = false;
      state.templatePickerQuery = "";
      $("template-picker-search").value = "";
      renderTemplatePickerOptions();
      $("template-picker-search").focus();
    }
  });

  $("button-picker-search").addEventListener("input", (e) => {
    state.buttonPickerQuery = e.target.value;
    renderButtonPickerOptions();
  });

  $("template-picker-search").addEventListener("input", (e) => {
    state.templatePickerQuery = e.target.value;
    renderTemplatePickerOptions();
  });

  $("button-picker-options").addEventListener("click", async (e) => {
    const opt = e.target.closest("[data-element-id]");
    if (!opt || !state.activeScenario) return;
    try {
      await addElementToScenario(Number(opt.dataset.elementId));
      closePickers();
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("template-picker-options").addEventListener("click", async (e) => {
    const opt = e.target.closest("[data-template-id]");
    if (!opt || !state.activeScenario) return;
    try {
      await applyTemplateToActive(Number(opt.dataset.templateId));
      closePickers();
    } catch (err) {
      toast(err.message, true);
    }
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".fancy-picker")) closePickers();
  });

  $("scenario-elements").addEventListener("click", async (e) => {
    const row = e.target.closest(".scenario-row");
    if (!row || !state.activeScenario) return;
    const id = Number(row.dataset.id);
    try {
      const toggleBtn = e.target.closest('[data-act="toggle"]');
      if (toggleBtn) {
        const nextVisible = !toggleBtn.classList.contains("is-on");
        if (isDraft()) {
          const el = state.activeScenario.elements.find(
            (x) => x.element_id === id
          );
          if (el) el.is_visible = nextVisible;
          renderScenarioElements();
          refreshExport();
          return;
        }
        state.activeScenario = await api(
          `/api/scenarios/${state.activeScenario.id}/elements/${id}`,
          {
            method: "PUT",
            body: JSON.stringify({ is_visible: nextVisible }),
          }
        );
        renderScenarioElements();
        await refreshExport();
        return;
      }
      if (e.target.closest('[data-act="remove"]')) {
        if (isDraft()) {
          state.activeScenario.elements = state.activeScenario.elements.filter(
            (x) => x.element_id !== id
          );
          renderWorkspace();
          refreshExport();
          return;
        }
        state.activeScenario = await api(
          `/api/scenarios/${state.activeScenario.id}/elements/${id}`,
          { method: "DELETE" }
        );
        renderWorkspace();
        await refreshExport();
      }
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("open-button-modal").addEventListener("click", () => openButtonModal());

  $("button-modal").addEventListener("click", (e) => {
    if (e.target.closest("[data-close-modal]")) {
      closeButtonModal();
      return;
    }
    const groupBtn = e.target.closest("[data-toggle-group]");
    if (groupBtn) {
      const group = groupBtn.dataset.toggleGroup;
      const turnOn = groupBtn.dataset.on === "1";
      const ids = state.elements
        .filter((el) => groupNameOf(el) === group)
        .map((el) => el.id);
      const set = new Set(state.templateSelectedIds);
      ids.forEach((id) => (turnOn ? set.add(id) : set.delete(id)));
      state.templateSelectedIds = [...set];
      renderButtonModal();
      return;
    }
  });

  $("button-modal-list").addEventListener("change", (e) => {
    const input = e.target.closest('input[type="checkbox"]');
    if (!input) return;
    const id = Number(input.value);
    const set = new Set(state.templateSelectedIds);
    if (input.checked) set.add(id);
    else set.delete(id);
    state.templateSelectedIds = [...set];
    renderButtonModal();
  });

  $("button-modal-search").addEventListener("input", (e) => {
    state.buttonModalQuery = e.target.value;
    renderButtonModal();
  });

  $("button-modal-clear").addEventListener("click", () => {
    state.templateSelectedIds = [];
    renderButtonModal();
  });

  $("button-modal-done").addEventListener("click", () => closeButtonModal());

  $("selected-buttons-preview").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-remove-id]");
    if (!btn) return;
    const id = Number(btn.dataset.removeId);
    state.templateSelectedIds = state.templateSelectedIds.filter((x) => x !== id);
    renderPicker();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("button-modal").hidden) closeButtonModal();
  });

  $("template-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setError("template-error", "");
    const form = e.target;
    const element_ids = [...state.templateSelectedIds];
    const payload = {
      name: form.name.value,
      description: form.description.value,
      element_ids,
    };
    try {
      if (state.editingTemplateId) {
        await api(`/api/templates/${state.editingTemplateId}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        toast("Template updated");
      } else {
        await api("/api/templates", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        toast("Template saved");
      }
      setTemplateFormMode(null);
      await loadTemplates();
    } catch (err) {
      setError("template-error", err.message);
    }
  });

  $("template-cancel-edit").addEventListener("click", () => {
    setTemplateFormMode(null);
    setError("template-error", "");
  });

  $("templates-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const id = Number(btn.closest(".item").dataset.id);
    const template = state.templates.find((t) => t.id === id);
    try {
      if (btn.dataset.act === "edit") {
        if (!template) return;
        setTemplateFormMode(template);
        showView("templates");
      } else if (btn.dataset.act === "del") {
        if (!confirm("Delete this template?")) return;
        await api(`/api/templates/${id}`, { method: "DELETE" });
        if (state.editingTemplateId === id) setTemplateFormMode(null);
        await loadTemplates();
        toast("Template deleted");
      } else if (btn.dataset.act === "use") {
        await useTemplate(id);
      }
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("copy-python-btn").addEventListener("click", () =>
    copy(state.export.python, "Python")
  );
  $("copy-json-btn").addEventListener("click", () =>
    copy(state.export.json, "JSON")
  );

  async function init() {
    try {
      await Promise.all([loadElements(), loadTemplates(), loadScenarios()]);
      const initial = viewFromHash();
      history.replaceState({ view: initial }, "", `#${initial}`);
      showView(initial, { skipHistory: true });
    } catch (err) {
      toast(err.message, true);
    }
  }

  init();
})();
