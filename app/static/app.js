const playground = document.querySelector("[data-playground]");

if (playground) {
  const form = playground.querySelector("[data-upload-form]");
  const input = playground.querySelector("[data-file-input]");
  const dropZone = playground.querySelector("[data-drop-zone]");
  const preview = playground.querySelector("[data-file-preview]");
  const image = playground.querySelector("[data-image-preview]");
  const fileName = playground.querySelector("[data-file-name]");
  const fileSize = playground.querySelector("[data-file-size]");
  const removeButton = playground.querySelector("[data-remove-file]");
  const submitButton = playground.querySelector("[data-submit]");
  const panel = playground.querySelector("[data-result-panel]");
  const emptyState = playground.querySelector("[data-empty-state]");
  const loadingState = playground.querySelector("[data-loading-state]");
  const errorState = playground.querySelector("[data-error-state]");
  const successState = playground.querySelector("[data-success-state]");
  const errorMessage = playground.querySelector("[data-error-message]");
  const errorRequest = playground.querySelector("[data-error-request]");
  const jsonOutput = playground.querySelector("[data-json-output]");
  const requestId = playground.querySelector("[data-request-id]");
  const demoLabel = playground.querySelector("[data-demo-label]");
  const copyFeedback = playground.querySelector("[data-copy-feedback]");
  let result = null;
  let previewUrl = null;

  const setState = (state) => {
    emptyState.hidden = state !== "empty";
    loadingState.hidden = state !== "loading";
    errorState.hidden = state !== "error";
    successState.hidden = state !== "success";
    panel.setAttribute("aria-busy", state === "loading" ? "true" : "false");
  };

  const clearPreviewUrl = () => {
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = null;
    }
  };

  const showFile = () => {
    const file = input.files[0];
    clearPreviewUrl();
    if (!file) {
      preview.hidden = true;
      image.hidden = true;
      image.removeAttribute("src");
      submitButton.disabled = true;
      return;
    }
    fileName.textContent = file.name;
    fileSize.textContent = `${(file.size / 1024 / 1024).toFixed(2)} MB`;
    preview.hidden = false;
    submitButton.disabled = false;
    if (file.type.startsWith("image/")) {
      previewUrl = URL.createObjectURL(file);
      image.src = previewUrl;
      image.hidden = false;
    } else {
      image.hidden = true;
      image.removeAttribute("src");
    }
  };

  const reset = () => {
    form.reset();
    result = null;
    jsonOutput.textContent = "";
    copyFeedback.textContent = "";
    clearPreviewUrl();
    showFile();
    setState("empty");
    input.focus();
  };

  input.addEventListener("change", showFile);
  removeButton.addEventListener("click", reset);
  playground.querySelector("[data-reset]").addEventListener("click", reset);

  ["dragenter", "dragover"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("is-dragging");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.remove("is-dragging");
    });
  });
  dropZone.addEventListener("drop", (event) => {
    if (event.dataTransfer.files.length) {
      input.files = event.dataTransfer.files;
      showFile();
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = input.files[0];
    if (!file) return;
    if (file.size > Number(playground.dataset.maxBytes)) {
      errorMessage.textContent = "Ukuran file melampaui batas teknologi.";
      errorRequest.textContent = "";
      setState("error");
      return;
    }

    submitButton.disabled = true;
    setState("loading");
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch(playground.dataset.endpoint, { method: "POST", body });
      const payload = await response.json();
      if (!response.ok) {
        throw payload;
      }
      result = payload;
      playground.querySelectorAll("[data-result-key]").forEach((row) => {
        const value = payload.data[row.dataset.resultKey];
        row.querySelector("td").textContent =
          value === null || value === undefined || value === ""
            ? "Tidak tersedia"
            : typeof value === "object"
              ? JSON.stringify(value)
              : String(value);
      });
      jsonOutput.textContent = JSON.stringify(payload, null, 2);
      requestId.textContent = `Request ID: ${payload.request_id}`;
      demoLabel.hidden = !payload.demo;
      setState("success");
    } catch (error) {
      const message = error?.error?.message || "Terjadi gangguan pada layanan. Silakan coba kembali.";
      errorMessage.textContent = message;
      errorRequest.textContent = error?.request_id ? `Request ID: ${error.request_id}` : "";
      setState("error");
    } finally {
      submitButton.disabled = false;
    }
  });

  playground.querySelector("[data-copy-json]").addEventListener("click", async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(result, null, 2));
      copyFeedback.textContent = "JSON disalin.";
    } catch {
      copyFeedback.textContent = "Browser tidak mengizinkan penyalinan otomatis.";
    }
  });

  playground.querySelector("[data-download-json]").addEventListener("click", () => {
    if (!result) return;
    const blobUrl = URL.createObjectURL(
      new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = blobUrl;
    link.download = "lutung-result.json";
    link.click();
    URL.revokeObjectURL(blobUrl);
  });
}
