import { copyFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

// The management UI remains the single source for the tested PCM transport.
const destination = new URL('../public/', import.meta.url);
await mkdir(destination, { recursive: true });
for (const name of ['voice_call.js', 'voice_pcm_worklet.js', 'voice_pcm_output_worklet.js']) {
  await copyFile(new URL('../../web/' + name, import.meta.url), new URL(name, destination));
}
console.log('Synced shared voice client and worklets to ' + fileURLToPath(destination));
