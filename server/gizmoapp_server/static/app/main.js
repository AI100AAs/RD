import { requestJson } from "./api.js";


function bootstrap() {
  const runtime = window.GizmoAppRuntime;
  if (!runtime) {
    throw new Error("The shared app runtime did not load.");
  }
  const config = runtime.readConfig();
  const currentUrl = new URL(window.location.href);
  const requestedArchiveId = currentUrl.searchParams.get("history") || "";
  const archiveId = /^[a-f0-9-]{16,64}$/.test(requestedArchiveId)
    ? requestedArchiveId
    : config.historyId || crypto.randomUUID();
  currentUrl.searchParams.set("history", archiveId);
  window.history.replaceState({}, "", currentUrl);
  document.querySelectorAll("a.history-link").forEach((link) => {
    const target = new URL(link.href, window.location.href);
    target.searchParams.set("history", archiveId);
    link.href = target.href;
  });
  const form = document.getElementById("design-form");
  const photoInput = document.getElementById("room-photo");
  const photoPreview = document.getElementById("photo-preview");
  const uploadZone = document.getElementById("upload-zone");
  const approvalPanel = document.getElementById("approval-panel");
  const refinedPrompt = document.getElementById("refined-prompt");
  const resultsSection = document.getElementById("results-section");
  const resultsGrid = document.getElementById("results-grid");
  const resultsTitle = document.getElementById("results-title");
  const moreButton = document.getElementById("more-button");
  const status = document.getElementById("form-status");
  const approvalStatus = document.getElementById("approval-status");
  const refineProgress = createProgress(document.getElementById("refine-progress"));
  const generateProgress = createProgress(document.getElementById("generate-progress"));
  let previewUrl = null;

  const themeToggle = document.getElementById("theme-toggle");
  const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  const setTheme = (theme) => {
    document.documentElement.dataset.theme = theme;
    const dark = theme === "dark";
    themeToggle.setAttribute("aria-pressed", String(dark));
    themeToggle.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    themeToggle.querySelector(".theme-toggle-label").textContent = dark ? "Light mode" : "Dark mode";
  };
  setTheme(prefersDark ? "dark" : "light");
  themeToggle.addEventListener("click", () => {
    const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    setTheme(nextTheme);
  });

  function selectedDirection() {
    const presets = [
      ["room", document.getElementById("room-type").value],
      ["style", document.getElementById("design-style").value],
      ["feeling", document.getElementById("room-feeling").value],
    ].filter(([, value]) => value);
    const brief = document.getElementById("room-brief").value.trim();
    const presetBrief = presets.map(([label, value]) => `${label}: ${value}`).join(", ");
    return [presetBrief, brief].filter(Boolean).join(". ");
  }

  const setStatus = (element, message, error = false) => {
    element.textContent = message;
    element.classList.toggle("is-error", error);
  };
  const setBusy = (button, busy, label) => {
    button.disabled = busy;
    button.classList.toggle("is-busy", busy);
    if (busy) button.dataset.label = button.textContent;
    button.textContent = busy ? label : button.dataset.label;
  };
  function createProgress(element) {
    const track = element.querySelector(".progress-track");
    const fill = element.querySelector(".progress-fill");
    const label = element.querySelector("[id$='progress-label']");
    const percent = element.querySelector("[id$='progress-percent']");
    let timer = null;
    let value = 0;
    let phase = 0;
    return {
      start(phases) {
        window.clearInterval(timer);
        value = 8;
        phase = 0;
        element.hidden = false;
        track.classList.add("is-active");
        this.update(phases[phase], value);
        timer = window.setInterval(() => {
          value = Math.min(88, value + (value < 55 ? 3 : 1));
          if (value % 15 < 2 && phase < phases.length - 1) phase += 1;
          this.update(phases[phase], value);
        }, 900);
      },
      update(nextLabel, nextValue) {
        fill.style.width = `${nextValue}%`;
        percent.textContent = `${nextValue}%`;
        label.textContent = nextLabel;
        track.setAttribute("aria-valuenow", String(nextValue));
      },
      finish(message) {
        window.clearInterval(timer);
        timer = null;
        track.classList.remove("is-active");
        this.update(message, 100);
        window.setTimeout(() => { element.hidden = true; }, 800);
      },
      fail() {
        window.clearInterval(timer);
        timer = null;
        track.classList.remove("is-active");
        this.update("Could not complete", value);
      },
    };
  }
  photoInput.addEventListener("change", () => {
    const file = photoInput.files[0];
    if (!file) return;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    photoPreview.src = previewUrl;
    photoPreview.hidden = false;
    uploadZone.classList.add("has-photo");
    document.getElementById("upload-title").textContent = file.name;
    document.getElementById("upload-hint").textContent = "Ready to transform - click to replace";
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = photoInput.files[0];
    const brief = selectedDirection();
    if (!file || !brief) {
      setStatus(status, "Add a photo and choose a preset or write a short direction first.", true);
      return;
    }
    const button = document.getElementById("refine-button");
    setBusy(button, true, "Refining your direction...");
    refineProgress.start(["Reading your room photo", "Shaping your direction", "Almost ready"]);
    setStatus(status, "Our design assistant is finding the details that matter.");
    try {
      const body = new FormData();
      body.append("photo", file);
      body.append("brief", brief);
      const payload = await requestJson(`${config.apiBase}/room/refine`, {
        method: "POST",
        body,
        timeoutMs: Math.max(config.requestTimeoutMs, 30000),
      });
      refinedPrompt.value = payload.prompt;
      approvalPanel.hidden = false;
      approvalPanel.scrollIntoView({ behavior: "smooth", block: "center" });
      setStatus(status, "Direction ready for your review.");
      refineProgress.finish("Ready to review");
    } catch (error) {
      setStatus(status, error.message, true);
      refineProgress.fail();
    } finally {
      setBusy(button, false);
    }
  });
  document.getElementById("back-button").addEventListener("click", () => {
    approvalPanel.hidden = true;
    setStatus(approvalStatus, "");
  });
  async function generateBatch(startOption, button, resetResults = false) {
    const file = photoInput.files[0];
    const prompt = refinedPrompt.value.trim();
    if (!file || !prompt) return;
    const body = new FormData();
    body.append("photo", file);
    body.append("prompt", prompt);
    body.append("start_option", String(startOption));
    body.append("history", archiveId);
    setBusy(button, true, "Creating directions...");
    generateProgress.start(["Preparing your room", `Creating directions ${startOption}-${startOption + 1}`]);
    setStatus(approvalStatus, "Rendering can take a little while. Keep this tab open.");
    if (resetResults) resultsGrid.innerHTML = "";
    resultsSection.hidden = false;
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
    let imageCount = 0;
    let requestTimeout = null;
    try {
      const controller = new AbortController();
      requestTimeout = window.setTimeout(() => controller.abort(), Math.max(config.requestTimeoutMs, 180000));
      const response = await fetch(`${config.apiBase}/room/generate`, {
        method: "POST",
        body,
        headers: { Accept: "text/event-stream" },
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error("The redesign service could not be reached. Check that the preview is online, then try again.");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const messages = buffer.split("\n\n");
        buffer = messages.pop();
        for (const message of messages) {
          const event = message.match(/^event: ([^\n]+)\ndata: ([\s\S]+)$/);
          if (!event) continue;
          const data = JSON.parse(event[2]);
          if (event[1] === "image") {
            imageCount += 1;
            appendResult(data);
            generateProgress.update(`Direction ${imageCount} ready`, Math.min(96, 20 + imageCount * 25));
            setStatus(approvalStatus, `${imageCount} direction${imageCount === 1 ? " is" : "s are"} ready. More may still be coming.`);
          } else if (event[1] === "progress") {
            setStatus(approvalStatus, `Creating direction ${data.option - startOption + 1} of 2...`);
          } else if (event[1] === "variation-error") {
            setStatus(approvalStatus, data.message || `${imageCount} direction${imageCount === 1 ? " is" : "s are"} ready. One variation could not be created.`, true);
          } else if (event[1] === "error") {
            throw new Error(data.message);
          }
        }
        if (done) break;
      }
      if (!imageCount) throw new Error("The redesign service returned no images. Please try again.");
      setStatus(approvalStatus, `${imageCount} new direction${imageCount === 1 ? " is" : "s are"} ready to explore.`);
      generateProgress.finish("Directions ready");
      moreButton.hidden = false;
    } catch (error) {
      const message = error?.name === "AbortError"
        ? "This is taking longer than expected. The image worker may be busy; please try again."
        : error.message;
      setStatus(approvalStatus, message, true);
      if (imageCount) {
        generateProgress.finish("Partial results ready");
        moreButton.hidden = false;
      } else generateProgress.fail();
    } finally {
      window.clearTimeout(requestTimeout);
      setBusy(button, false);
    }
  }
  document.getElementById("generate-button").addEventListener("click", () => {
    generateBatch(1, document.getElementById("generate-button"), true);
  });
  function appendResult(image) {
    const option = Number(image.option) || (resultsGrid.children.length + 1);
    const source = `data:${image.contentType};base64,${image.data}`;
    resultsGrid.insertAdjacentHTML("beforeend", `<article class="result-card"><img src="${source}" alt="Room redesign option ${option}"><div class="result-label"><span>OPTION ${String(option).padStart(2, "0")}</span><a href="${source}" download="roomform-option-${option}.png">Download</a></div></article>`);
    if (resultsGrid.children.length > 2) resultsTitle.textContent = `${resultsGrid.children.length} ways forward`;
  }
  async function restoreSavedResults() {
    try {
      const payload = await requestJson(`${config.apiBase}/room/history?history=${encodeURIComponent(archiveId)}`);
      if (!payload.creations?.length) return;
      resultsGrid.innerHTML = "";
      [...payload.creations].reverse().forEach((creation) => appendResult({
        option: creation.option_number,
        contentType: creation.content_type,
        data: creation.image_data,
      }));
      resultsTitle.textContent = `${resultsGrid.children.length} saved possibilities`;
      resultsSection.hidden = false;
      setStatus(approvalStatus, "Your saved directions are back.");
    } catch {
      // The studio remains usable if history is unavailable.
    }
  }
  document.getElementById("new-design-button").addEventListener("click", () => {
    resultsSection.hidden = true;
    approvalPanel.hidden = true;
    moreButton.hidden = true;
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  moreButton.addEventListener("click", () => {
    generateBatch(resultsGrid.children.length + 1, moreButton);
  });
  runtime.markReady();
  restoreSavedResults();
}


try {
  bootstrap();
} catch (error) {
  window.GizmoAppRuntime?.showFatalError(error);
}
