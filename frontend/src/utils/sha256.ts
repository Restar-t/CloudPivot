/** 纯 SHA-256 实现（无依赖）：Worker 与主线程共用。 */
export type ProgressFn = (percent: number) => void;

export async function sha256Stream(
  file: File,
  chunkSize: number,
  onProgress: (done: number) => void,
): Promise<string> {
  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);

  let h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a,
      h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
  let processed = 0;

  const w = new Uint32Array(64);
  const processBlock = (block: Uint8Array) => {
    for (let i = 0; i < 16; i++) {
      w[i] = ((block[i * 4] << 24) | (block[i * 4 + 1] << 16) | (block[i * 4 + 2] << 8) | block[i * 4 + 3]) >>> 0;
    }
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + K[i] + w[i]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) >>> 0;
      h = g; g = f; f = e; e = (d + t1) >>> 0;
      d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    h0 = (h0 + a) >>> 0; h1 = (h1 + b) >>> 0; h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0; h5 = (h5 + f) >>> 0; h6 = (h6 + g) >>> 0; h7 = (h7 + h) >>> 0;
  };
  const rotr = (x: number, n: number) => ((x >>> n) | (x << (32 - n))) >>> 0;

  // 逐块读取 + 填充
  let pending = new Uint8Array(0);
  for (let offset = 0; offset < file.size; offset += chunkSize) {
    const slice = file.slice(offset, offset + chunkSize);
    const buf = new Uint8Array(await slice.arrayBuffer());
    // 拼接 pending（长度 < 64）
    const combined = new Uint8Array(pending.length + buf.length);
    combined.set(pending); combined.set(buf, pending.length);
    const full = Math.floor(combined.length / 64) * 64;
    for (let i = 0; i < full; i += 64) processBlock(combined.subarray(i, i + 64));
    pending = combined.subarray(full);
    processed += buf.length;
    if (processed % (chunkSize * 4) < chunkSize) onProgress(processed);
    // 让出事件循环，避免卡死 worker 消息循环
    await new Promise((r) => setTimeout(r, 0));
  }

  // padding
  const lenBitsHi = Math.floor(file.size * 8 / 4294967296) >>> 0;
  const lenBitsLo = (file.size * 8) >>> 0;
  pending = appendPadding(pending, lenBitsHi, lenBitsLo);
  for (let i = 0; i < pending.length; i += 64) processBlock(pending.subarray(i, i + 64));

  const hex = (n: number) => n.toString(16).padStart(8, "0");
  return hex(h0) + hex(h1) + hex(h2) + hex(h3) + hex(h4) + hex(h5) + hex(h6) + hex(h7);
}

function appendPadding(pending: Uint8Array, lenHi: number, lenLo: number): Uint8Array {
  const padLen = ((pending.length + 9 + 63) & ~63) - pending.length; // 至少 1 字节 0x80 + 8 字节长度
  const padded = new Uint8Array(pending.length + padLen);
  padded.set(pending);
  padded[pending.length] = 0x80;
  const dv = new DataView(padded.buffer);
  dv.setUint32(padded.length + padLen - 8, lenHi);
  dv.setUint32(padded.length + padLen - 4, lenLo);
  return padded;
}


/** Blob（分片）哈希：主线程用于分片校验和。 */
export async function hashBlobSha256(blob: Blob): Promise<string> {
  const buf = new Uint8Array(await blob.arrayBuffer());
  // 复用流式实现：单块喂入
  const fake = new FakeFileLike(buf);
  return sha256Stream(fake as unknown as File, buf.length || 1, () => undefined);
}

class FakeFileLike {
  size: number;
  constructor(private buf: Uint8Array) { this.size = buf.length; }
  slice(): Blob {
    return new Blob([this.buf]);
  }
}
