// LiveKit connection helper for the voice session.
// Connects to the room with the backend-issued token, publishes the mic, and
// plays the agent's audio track. The slide viewer + manual controls work without
// this — voice is an enhancement layered on top.

import {
  Room,
  RoomEvent,
  Track,
  type RemoteTrack,
  type RemoteAudioTrack,
} from "livekit-client";

export interface VoiceConnection {
  room: Room;
  disconnect: () => Promise<void>;
}

/**
 * Join the LiveKit room, enable the microphone, and attach inbound agent audio
 * to a hidden <audio> element so the user hears the presentation.
 * Returns the room handle and a disconnect function.
 */
export async function connectVoice(
  url: string,
  token: string,
  audioEl: HTMLAudioElement,
): Promise<VoiceConnection> {
  const room = new Room({ adaptiveStream: true, dynacast: true });

  room.on(RoomEvent.TrackSubscribed, (track: RemoteTrack) => {
    if (track.kind === Track.Kind.Audio) {
      (track as RemoteAudioTrack).attach(audioEl);
    }
  });

  await room.connect(url, token);
  await room.localParticipant.setMicrophoneEnabled(true);
  return {
    room,
    disconnect: async () => {
      await room.disconnect();
    },
  };
}
