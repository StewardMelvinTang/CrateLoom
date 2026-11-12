import { invoke } from '@tauri-apps/api/core';

/** One request to the Python engine through the desktop bridge. Rejects with the engine's message. */
export const call = <T,>(method: string, params: object = {}) => invoke<T>('engine_call', { method, params });

/** Engine and bridge failures arrive as strings or Errors; show the message without an "Error:" prefix. */
export const message = (error: unknown) => (error instanceof Error ? error.message : String(error)).replace(/^Error:\s*/, '');
