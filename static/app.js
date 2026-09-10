const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#video");
const fileLabel = document.querySelector("#file-label");
const button = document.querySelector("#submit-button");
const cancelButton = document.querySelector("#cancel-button");
const statusBox = document.querySelector("#status");
const dropZone = document.querySelector("#drop-zone");
const progressPanel = document.querySelector("#progress-panel");
const progressTrack = document.querySelector("#progress-track");
const progressFill = document.querySelector("#progress-fill");
const progressLabel = document.querySelector("#progress-label");
const progressValue = document.querySelector("#progress-value");
let uploadController = null;
let currentJobId = null;

function showStatus(message, kind = "working") {
  statusBox.hidden = false;
  statusBox.className = `status ${kind}`;
  statusBox.innerHTML = message;
}

function updateProgress(value, label, status) {
  progressPanel.hidden = false;
  progressFill.style.width = `${value}%`;
  progressTrack.setAttribute("aria-valuenow", value);
  progressLabel.textContent = label;
  progressValue.textContent = `${value}%`;
  progressTrack.classList.toggle("is-separating", status === "processing" && value >= 22 && value < 92);
  progressTrack.classList.toggle("is-complete", status === "complete");
}

function hideCancel() {
  cancelButton.hidden = true;
  cancelButton.disabled = false;
}

function resetUploadState() {
  button.disabled = false;
  currentJobId = null;
  uploadController = null;
  fileInput.value = "";
  fileLabel.textContent = "Choose a video or drop it here";
  progressPanel.hidden = true;
  hideCancel();
}

function chooseFile(file) {
  if (!file) return;
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  fileLabel.textContent = file.name;
  cancelButton.textContent = "Clear selection";
  cancelButton.hidden = false;
}

fileInput.addEventListener("change", () => chooseFile(fileInput.files[0]));
["dragenter", "dragover"].forEach((event) => dropZone.addEventListener(event, (e) => {
  e.preventDefault();
  dropZone.classList.add("dragging");
}));
["dragleave", "drop"].forEach((event) => dropZone.addEventListener(event, (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragging");
}));
dropZone.addEventListener("drop", (event) => chooseFile(event.dataTransfer.files[0]));

async function pollJob(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  const job = await response.json();
  if (!response.ok) throw new Error(job.detail || "Could not check processing status.");

  if (job.status === "complete") {
    button.disabled = false;
    hideCancel();
    updateProgress(100, "Finished", job.status);
    showStatus(`${job.message}<a class="download" href="/api/jobs/${jobId}/download">Download your video</a>`, "complete");
    return;
  }
  if (job.status === "failed") {
    resetUploadState();
    progressPanel.hidden = true;
    showStatus(job.message, "failed");
    return;
  }
  if (job.status === "cancelled") {
    resetUploadState();
    showStatus(job.message, "failed");
    return;
  }
  updateProgress(job.progress, job.message, job.status);
  showStatus(job.message, "working");
  window.setTimeout(() => pollJob(jobId).catch(showError), 1500);
}

function showError(error) {
  button.disabled = false;
  hideCancel();
  showStatus(error.message || "Something went wrong. Please try again.", "failed");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!fileInput.files[0]) return;
  button.disabled = true;
  currentJobId = null;
  uploadController = new AbortController();
  cancelButton.textContent = "Cancel upload";
  cancelButton.hidden = false;
  updateProgress(1, "Uploading your video…", "uploading");
  showStatus("Uploading your video…", "working");
  try {
    const response = await fetch("/api/jobs", { method: "POST", body: new FormData(form), signal: uploadController.signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Upload failed.");
    currentJobId = data.job_id;
    uploadController = null;
    cancelButton.textContent = "Cancel processing";
    pollJob(currentJobId).catch(showError);
  } catch (error) {
    if (error.name === "AbortError") {
      resetUploadState();
      showStatus("Upload cancelled.", "failed");
      return;
    }
    showError(error);
  }
});

cancelButton.addEventListener("click", async () => {
  if (uploadController) {
    uploadController.abort();
    return;
  }
  if (!currentJobId) {
    resetUploadState();
    showStatus("Selection cleared.", "working");
    return;
  }
  cancelButton.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${currentJobId}`, { method: "DELETE" });
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || "Could not cancel processing.");
    showStatus(job.message, "working");
    pollJob(currentJobId).catch(showError);
  } catch (error) {
    cancelButton.disabled = false;
    showError(error);
  }
});
