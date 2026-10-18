async function checkHealth() {
  const text = document.getElementById("healthText");
  const dot = document.getElementById("healthDot");

  if (!text || !dot) return;

  try {
    const response = await fetch("/health");
    if (!response.ok) throw new Error("health check failed");
    const data = await response.json();
    text.textContent = data.status === "ok" ? "服务运行正常" : "服务状态异常";
    dot.classList.toggle("ok", data.status === "ok");
  } catch (error) {
    text.textContent = "服务连接失败";
    dot.classList.remove("ok");
  }
}

async function loadCurrentUser() {
  const userTarget = document.getElementById("currentUser");
  if (!userTarget) return;

  try {
    const response = await fetch("/api/me");
    if (!response.ok) return;
    const data = await response.json();
    if (data.authenticated && data.user) {
      userTarget.textContent = data.user.username;
    }
  } catch (error) {
    userTarget.textContent = "";
  }
}

function formatBytes(size) {
  if (!Number.isFinite(size) || size < 1024) return `${size || 0} B`;
  const units = ["KB", "MB", "GB"];
  let value = size / 1024;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => {
    const chars = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return chars[char];
  });
}

async function loadDocuments() {
  const tableBody = document.getElementById("documentsTableBody");
  if (!tableBody) return;

  try {
    const response = await fetch("/api/documents");
    if (!response.ok) throw new Error("load documents failed");

    const data = await response.json();
    const documents = Array.isArray(data.documents) ? data.documents : [];

    if (documents.length === 0) {
      tableBody.innerHTML = "<tr><td colspan='6'>还没有上传过文档。</td></tr>";
      return;
    }

    tableBody.innerHTML = documents
      .map(
        (document) => `
          <tr>
            <td>${document.id}</td>
            <td>${escapeHtml(document.filename)}</td>
            <td>${escapeHtml(document.content_type)}</td>
            <td>${formatBytes(document.size_bytes)}</td>
            <td>${escapeHtml(document.uploaded_by_username)}</td>
            <td>${escapeHtml(document.uploaded_at)}</td>
          </tr>
        `,
      )
      .join("");
  } catch (error) {
    tableBody.innerHTML = "<tr><td colspan='6'>文档列表加载失败。</td></tr>";
  }
}

function setupDocumentUploadForm() {
  const form = document.getElementById("documentUploadForm");
  const error = document.getElementById("documentUploadError");
  const success = document.getElementById("documentUploadSuccess");
  if (!form || !error || !success) return;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    success.textContent = "";

    const fileInput = form.querySelector("input[type='file']");
    const submitButton = form.querySelector("button[type='submit']");
    const file = fileInput?.files?.[0];

    if (!file) {
      error.textContent = "请选择要上传的文档。";
      return;
    }

    submitButton.disabled = true;

    try {
      const buffer = await file.arrayBuffer();
      const bytes = new Uint8Array(buffer);
      let binary = "";

      for (const byte of bytes) {
        binary += String.fromCharCode(byte);
      }

      const response = await fetch("/api/documents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: file.name,
          content_type: file.type || "application/octet-stream",
          content_base64: btoa(binary),
        }),
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "upload failed");
      }

      success.textContent = `上传成功：${file.name}`;
      form.reset();
      loadDocuments();
    } catch (uploadError) {
      error.textContent = uploadError.message || "上传失败，请稍后重试。";
    } finally {
      submitButton.disabled = false;
    }
  });
}

function setupLoginForm() {
  const form = document.getElementById("loginForm");
  const error = document.getElementById("loginError");
  if (!form || !error) return;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";

    const submitButton = form.querySelector("button[type='submit']");
    submitButton.disabled = true;

    try {
      const formData = new FormData(form);
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: formData.get("username"),
          password: formData.get("password"),
        }),
      });

      if (!response.ok) {
        error.textContent = "Invalid username or password.";
        return;
      }

      window.location.href = "/";
    } catch (loginError) {
      error.textContent = "Login failed. Try again.";
    } finally {
      submitButton.disabled = false;
    }
  });
}

function setupLogout() {
  const button = document.getElementById("logoutButton");
  if (!button) return;

  button.addEventListener("click", async () => {
    await fetch("/api/logout", { method: "POST" });
    window.location.href = "/login";
  });
}

checkHealth();
loadCurrentUser();
loadDocuments();
setupLoginForm();
setupDocumentUploadForm();
setupLogout();
