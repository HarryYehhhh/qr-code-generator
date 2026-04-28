<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { getQRCode, deleteQRCode, testRedirect, updateQRCode } from "../api/qrCode";

const props = defineProps<{
  token: string;
  imageUrl: string;
  originalUrl: string;
}>();

const emit = defineEmits<{
  deleted: [token: string];
}>();

const currentUrl = ref(props.originalUrl);
const clickCount = ref(0);
const status = ref("active");
const createdAt = ref("");
const loading = ref(false);
const deleting = ref(false);
const testing = ref(false);
const editing = ref(false);
const updating = ref(false);
const editUrl = ref("");
const redirectResult = ref<{ ok: boolean; status: number; location: string | null } | null>(null);
const error = ref("");
const updateSuccess = ref(false);

const shortUrl = computed(() => {
  return `http://localhost:8000/r/${props.token}`;
});

const refreshingCount = ref(false);

async function fetchInfo() {
  loading.value = true;
  error.value = "";
  try {
    const result = await getQRCode(props.token);
    if (result.status === 410) {
      status.value = "deleted";
      return;
    }
    if (result.status === 404) {
      error.value = "QR code not found";
      return;
    }
    if (result.data) {
      currentUrl.value = result.data.url;
      clickCount.value = result.data.click_count;
      status.value = result.data.status;
      createdAt.value = new Date(result.data.created_at).toLocaleString();
    }
  } catch (e: any) {
    error.value = e.message || "Failed to fetch info";
  } finally {
    loading.value = false;
  }
}

async function handleDelete() {
  deleting.value = true;
  error.value = "";
  try {
    await deleteQRCode(props.token);
    status.value = "deleted";
    emit("deleted", props.token);
  } catch (e: any) {
    error.value = e.message || "Failed to delete";
  } finally {
    deleting.value = false;
  }
}

async function handleTestRedirect() {
  testing.value = true;
  redirectResult.value = null;
  error.value = "";
  try {
    redirectResult.value = await testRedirect(props.token);
  } catch (e: any) {
    error.value = e.message || "Redirect test failed";
  } finally {
    testing.value = false;
  }
}

function startEdit() {
  editUrl.value = currentUrl.value;
  editing.value = true;
  updateSuccess.value = false;
}

function cancelEdit() {
  editing.value = false;
  editUrl.value = "";
}

async function handleUpdate() {
  const trimmed = editUrl.value.trim();
  if (!trimmed) return;
  updating.value = true;
  error.value = "";
  updateSuccess.value = false;
  try {
    await updateQRCode(props.token, trimmed);
    currentUrl.value = trimmed;
    editing.value = false;
    editUrl.value = "";
    updateSuccess.value = true;
    setTimeout(() => { updateSuccess.value = false; }, 3000);
  } catch (e: any) {
    error.value = e.message || "Failed to update URL";
  } finally {
    updating.value = false;
  }
}

function formatStatus(s: string): string {
  return s === "active" ? "Active" : "Deleted";
}

async function refreshCount() {
  refreshingCount.value = true;
  error.value = "";
  try {
    const result = await getQRCode(props.token);
    if (result.data) {
      clickCount.value = result.data.click_count;
    }
  } catch (e: any) {
    error.value = e.message || "Failed to fetch count";
  } finally {
    refreshingCount.value = false;
  }
}

onMounted(() => {
  fetchInfo();
});
</script>

<template>
  <div class="display">
    <div class="image-section">
      <img v-if="imageUrl" :src="imageUrl" :alt="`QR code for ${originalUrl}`" class="qr-image" />
      <div v-else class="qr-image qr-placeholder">No Image</div>
    </div>

    <div class="info-section">
      <div class="info-row">
        <span class="label">URL</span>
        <span class="url-text">{{ currentUrl }}</span>
      </div>

      <div class="info-row">
        <span class="label">Short URL</span>
        <a :href="shortUrl" target="_blank" class="url short-url">{{ shortUrl }}</a>
      </div>

      <div v-if="status === 'active' && !editing" class="info-row">
        <span class="label"></span>
        <button class="btn btn-edit" @click="startEdit">Edit URL</button>
      </div>

      <div v-if="editing" class="edit-section">
        <input
          v-model="editUrl"
          type="text"
          placeholder="https://new-url.com"
          class="edit-input"
          @keyup.enter="handleUpdate"
        />
        <div class="edit-actions">
          <button class="btn btn-primary" @click="handleUpdate" :disabled="updating">
            {{ updating ? "Saving..." : "Save" }}
          </button>
          <button class="btn btn-secondary" @click="cancelEdit" :disabled="updating">Cancel</button>
        </div>
      </div>

      <div v-if="updateSuccess" class="update-success">URL updated successfully</div>

      <div class="info-row">
        <span class="label">Status</span>
        <span class="badge" :class="status === 'active' ? 'badge-active' : 'badge-deleted'">
          {{ formatStatus(status) }}
        </span>
      </div>

      <div class="info-row">
        <span class="label">Scans</span>
        <span class="count">{{ clickCount }}</span>
        <button class="btn btn-count" @click="refreshCount" :disabled="refreshingCount">
          {{ refreshingCount ? "..." : "↻" }}
        </button>
      </div>

      <div v-if="createdAt" class="info-row">
        <span class="label">Created</span>
        <span>{{ createdAt }}</span>
      </div>

      <p v-if="error" class="error">{{ error }}</p>

      <div class="actions">
        <button
          class="btn btn-test"
          @click="handleTestRedirect"
          :disabled="testing"
        >
          {{ testing ? "Testing..." : "Test Redirect" }}
        </button>
        <button
          v-if="status === 'active'"
          class="btn btn-danger"
          @click="handleDelete"
          :disabled="deleting"
        >
          {{ deleting ? "Deleting..." : "Delete" }}
        </button>
      </div>

      <div v-if="redirectResult" class="redirect-result" :class="redirectResult.ok ? 'result-ok' : 'result-fail'">
        <span class="result-icon">{{ redirectResult.ok ? "&#10003;" : "&#10007;" }}</span>
        <span v-if="redirectResult.ok">
          302 Redirect confirmed → {{ currentUrl }}
        </span>
        <span v-else>
          Expected 302, got {{ redirectResult.status }}{{ redirectResult.status === 410 ? " (Deleted)" : "" }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.display {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 1.5rem;
  display: flex;
  gap: 1.5rem;
  align-items: flex-start;
}

.image-section {
  flex-shrink: 0;
}

.qr-image {
  width: 200px;
  height: 200px;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
}

.qr-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f3f4f6;
  color: #9ca3af;
  font-size: 0.875rem;
}

.info-section {
  flex: 1;
  min-width: 0;
}

.info-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.625rem;
  font-size: 0.925rem;
}

.label {
  font-weight: 600;
  color: #6b7280;
  min-width: 60px;
}


.badge {
  padding: 0.15rem 0.625rem;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 500;
}

.badge-active {
  background: #d1fae5;
  color: #065f46;
}

.badge-deleted {
  background: #fee2e2;
  color: #991b1b;
}

.count {
  font-weight: 700;
  font-size: 1.1rem;
  color: #1f2937;
}

.actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 1rem;
}

.btn {
  padding: 0.5rem 1rem;
  border: none;
  border-radius: 6px;
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 0.15s;
}

.btn-secondary {
  background: #f3f4f6;
  color: #374151;
}

.btn-secondary:hover:not(:disabled) {
  background: #e5e7eb;
}

.btn-test {
  background: #8b5cf6;
  color: #fff;
}

.btn-test:hover:not(:disabled) {
  background: #7c3aed;
}

.btn-danger {
  background: #ef4444;
  color: #fff;
}

.btn-danger:hover:not(:disabled) {
  background: #dc2626;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.redirect-result {
  margin-top: 0.75rem;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  font-size: 0.875rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.result-ok {
  background: #d1fae5;
  color: #065f46;
}

.result-fail {
  background: #fee2e2;
  color: #991b1b;
}

.result-icon {
  font-weight: 700;
  font-size: 1rem;
}

.url-text {
  word-break: break-all;
  color: #1f2937;
}

.short-url {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 0.85rem;
  color: #3b82f6;
  word-break: break-all;
}

.btn-count {
  background: none;
  color: #6b7280;
  padding: 0 0.25rem;
  font-size: 1rem;
  line-height: 1;
}

.btn-count:hover:not(:disabled) {
  color: #374151;
}

.btn-edit {
  background: none;
  color: #3b82f6;
  padding: 0;
  font-size: 0.825rem;
  text-decoration: underline;
}

.btn-edit:hover {
  color: #2563eb;
}

.btn-primary {
  background: #3b82f6;
  color: #fff;
}

.btn-primary:hover:not(:disabled) {
  background: #2563eb;
}

.edit-section {
  margin: 0.25rem 0 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.edit-input {
  padding: 0.5rem 0.75rem;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 0.9rem;
  outline: none;
  transition: border-color 0.15s;
}

.edit-input:focus {
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
}

.edit-actions {
  display: flex;
  gap: 0.5rem;
}

.update-success {
  margin-top: 0.25rem;
  padding: 0.375rem 0.75rem;
  background: #d1fae5;
  color: #065f46;
  border-radius: 6px;
  font-size: 0.85rem;
}

.error {
  color: #ef4444;
  font-size: 0.875rem;
  margin: 0.5rem 0 0;
}
</style>
