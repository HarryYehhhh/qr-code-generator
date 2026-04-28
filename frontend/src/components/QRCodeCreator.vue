<script setup lang="ts">
import { ref } from "vue";
import { createQRCode, getQRCodeImage } from "../api/qrCode";

const emit = defineEmits<{
  created: [token: string, imageUrl: string, url: string];
}>();

const url = ref("");
const loading = ref(false);
const error = ref("");

async function handleSubmit() {
  error.value = "";
  const trimmed = url.value.trim();
  if (!trimmed) {
    error.value = "Please enter a URL";
    return;
  }

  loading.value = true;
  try {
    const { qr_token } = await createQRCode(trimmed);
    const { image_location } = await getQRCodeImage(qr_token);
    emit("created", qr_token, image_location, trimmed);
    url.value = "";
  } catch (e: any) {
    error.value = e.message || "Failed to create QR code";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="creator">
    <h2>Generate QR Code</h2>
    <form @submit.prevent="handleSubmit" class="form">
      <input
        v-model="url"
        type="text"
        placeholder="https://example.com"
        class="input"
        :disabled="loading"
      />
      <button type="submit" class="btn btn-primary" :disabled="loading">
        {{ loading ? "Generating..." : "Generate" }}
      </button>
    </form>
    <p v-if="error" class="error">{{ error }}</p>
  </div>
</template>

<style scoped>
.creator {
  margin-bottom: 2rem;
}

h2 {
  margin: 0 0 1rem;
  font-size: 1.25rem;
  font-weight: 600;
}

.form {
  display: flex;
  gap: 0.5rem;
}

.input {
  flex: 1;
  padding: 0.625rem 0.75rem;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 0.95rem;
  outline: none;
  transition: border-color 0.15s;
}

.input:focus {
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
}

.btn {
  padding: 0.625rem 1.25rem;
  border: none;
  border-radius: 6px;
  font-size: 0.95rem;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 0.15s;
}

.btn-primary {
  background: #3b82f6;
  color: #fff;
}

.btn-primary:hover:not(:disabled) {
  background: #2563eb;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.error {
  margin-top: 0.5rem;
  color: #ef4444;
  font-size: 0.875rem;
}
</style>
