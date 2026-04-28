<script setup lang="ts">
import { ref, onMounted } from "vue";
import QRCodeCreator from "./components/QRCodeCreator.vue";
import QRCodeDisplay from "./components/QRCodeDisplay.vue";
import { listQRCodes, getQRCodeImage } from "./api/qrCode";

interface QRCodeEntry {
  token: string;
  imageUrl: string;
  url: string;
}

const entries = ref<QRCodeEntry[]>([]);
const loadingList = ref(false);

onMounted(async () => {
  loadingList.value = true;
  try {
    const list = await listQRCodes();
    const loaded = await Promise.all(
      list.map(async (item) => {
        let imageUrl = "";
        if (item.status === "active") {
          try {
            const { image_location } = await getQRCodeImage(item.qr_token);
            imageUrl = image_location;
          } catch { /* image unavailable */ }
        }
        return { token: item.qr_token, imageUrl, url: item.url };
      })
    );
    entries.value = loaded;
  } catch {
    // silently fail — user can still create new ones
  } finally {
    loadingList.value = false;
  }
});

function onCreated(token: string, imageUrl: string, url: string) {
  entries.value.unshift({ token, imageUrl, url });
}

function onDeleted(_token: string) {
  // Keep entry visible — QRCodeDisplay handles status update internally
}
</script>

<template>
  <div class="app">
    <header class="header">
      <h1>QR Code Generator</h1>
    </header>

    <main class="main">
      <QRCodeCreator @created="onCreated" />

      <p v-if="loadingList" class="loading">Loading QR codes...</p>
      <div v-else-if="entries.length" class="entries">
        <QRCodeDisplay
          v-for="entry in entries"
          :key="entry.token"
          :token="entry.token"
          :image-url="entry.imageUrl"
          :original-url="entry.url"
          @deleted="onDeleted"
        />
      </div>
      <p v-else class="empty">Enter a URL above to generate a QR code.</p>
    </main>
  </div>
</template>

<style scoped>
.app {
  max-width: 720px;
  margin: 0 auto;
  padding: 2rem 1rem;
}

.header {
  margin-bottom: 2rem;
}

.header h1 {
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0;
  color: #111827;
}

.main {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.entries {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.loading {
  color: #6b7280;
  text-align: center;
  padding: 2rem 0;
}

.empty {
  color: #9ca3af;
  text-align: center;
  padding: 3rem 0;
}
</style>
