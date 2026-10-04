import { sha256 } from "@noble/hashes/sha2.js";

/** Exact SHA-256 of original upload bytes, also available on insecure LAN HTTP origins. */
export async function sha256File(file: File): Promise<string> {
  const buffer = await new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(new Error("Không đọc được tệp."));
    reader.readAsArrayBuffer(file);
  });
  const bytes = new Uint8Array(buffer);
  const subtle = globalThis.crypto?.subtle;
  const digest = subtle ? new Uint8Array(await subtle.digest("SHA-256", bytes)) : sha256(bytes);
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
}
