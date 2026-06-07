import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';

// Speaker (bot output) mute state. The flag is shared between the header
// speaker button and the BotAudio sink (see SpeakerMute.tsx). Output only —
// mic mute is separate (PipecatClientMicToggle, by the composer).
interface SpeakerMuteCtx {
  muted: boolean;
  toggle: () => void;
  setMuted: (v: boolean) => void;
}

const Ctx = createContext<SpeakerMuteCtx | null>(null);

export const SpeakerMuteProvider = ({ children }: { children: ReactNode }) => {
  const [muted, setMuted] = useState(false);
  const toggle = useCallback(() => setMuted((m) => !m), []);
  return <Ctx.Provider value={{ muted, toggle, setMuted }}>{children}</Ctx.Provider>;
};

// Context + provider + hook in one file is the canonical React pattern; the
// fast-refresh rule only wants pure-component modules, so scope-disable it for
// the hook export (the provider still HMRs fine).
// eslint-disable-next-line react-refresh/only-export-components
export const useSpeakerMute = (): SpeakerMuteCtx => {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useSpeakerMute must be used within SpeakerMuteProvider');
  return ctx;
};
