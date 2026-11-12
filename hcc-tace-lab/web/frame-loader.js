// One image request at a time avoids concurrent DICOM decoding contention.
// Visible requests supersede queued slices. Prefetch runs only when idle.
export class FrameLoader {
  constructor(limit = 48) {
    this.limit = limit;
    this.cache = new Map();
    this.visible = new Map();
    this.prefetch = new Map();
    this.pinned = new Map();
    this.running = false;
    this.generation = 0;
  }

  cached(url) {
    const blob = this.cache.get(url);
    if (blob) { this.cache.delete(url); this.cache.set(url, blob); }
    return blob;
  }

  load(stage, url) {
    this.prefetch.delete(stage);
    this.visible.get(stage)?.resolve(null);
    this.visible.delete(stage);
    const cached = this.cached(url);
    if (cached) return Promise.resolve(cached);
    return new Promise((resolve, reject) => {
      this.visible.set(stage, {url, resolve, reject, generation: this.generation});
      this.pump();
    });
  }

  warm(stage, urls) {
    this.prefetch.set(stage, urls.filter(url => !this.cache.has(url)));
    this.pump();
  }

  pin(stage, url) {
    this.pinned.set(stage, url);
    this.trim();
  }

  trim() {
    const pinned = new Set(this.pinned.values());
    for (const [url, blob] of this.cache) {
      if (this.cache.size <= this.limit) break;
      if (pinned.has(url)) continue;
      URL.revokeObjectURL(blob);
      this.cache.delete(url);
    }
  }

  next() {
    for (const [stage, job] of this.visible) {
      this.visible.delete(stage);
      return job;
    }
    for (const urls of this.prefetch.values()) {
      while (urls.length) {
        const url = urls.shift();
        if (!this.cache.has(url)) {
          return {url, generation: this.generation, resolve() {}, reject() {}};
        }
      }
    }
    return null;
  }

  async pump() {
    if (this.running) return;
    this.running = true;
    try {
      let job;
      while ((job = this.next())) {
        try {
          let blob = this.cached(job.url);
          if (!blob) {
            const response = await fetch(job.url);
            if (!response.ok) throw new Error('Image request failed.');
            const payload = await response.blob();
            if (job.generation !== this.generation) { job.resolve(null); continue; }
            blob = URL.createObjectURL(payload);
            this.cache.set(job.url, blob);
            this.trim();
          }
          job.resolve(blob);
        } catch (error) { job.reject(error); }
      }
    } finally { this.running = false; }
  }

  clear() {
    this.generation++;
    for (const job of this.visible.values()) job.resolve(null);
    this.visible.clear();
    this.prefetch.clear();
    this.pinned.clear();
    for (const blob of this.cache.values()) URL.revokeObjectURL(blob);
    this.cache.clear();
  }
}
