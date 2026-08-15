const webxrNames = [
  "wrist",
  "thumb-metacarpal", "thumb-phalanx-proximal", "thumb-phalanx-distal", "thumb-tip",
  "index-finger-metacarpal", "index-finger-phalanx-proximal", "index-finger-phalanx-intermediate", "index-finger-phalanx-distal", "index-finger-tip",
  "middle-finger-metacarpal", "middle-finger-phalanx-proximal", "middle-finger-phalanx-intermediate", "middle-finger-phalanx-distal", "middle-finger-tip",
  "ring-finger-metacarpal", "ring-finger-phalanx-proximal", "ring-finger-phalanx-intermediate", "ring-finger-phalanx-distal", "ring-finger-tip",
  "pinky-finger-metacarpal", "pinky-finger-phalanx-proximal", "pinky-finger-phalanx-intermediate", "pinky-finger-phalanx-distal", "pinky-finger-tip",
];

const startButton = document.querySelector("#start");
const status = document.querySelector("#status");
const canvas = document.querySelector("#xr-canvas");
let socket = null;
let session = null;
let referenceSpace = null;
let gl = null;
let sequence = 0;

window.quest3TeleopState = {
  phase: "loading",
  error: "",
};

function setStatus(message) {
  status.textContent = message;
}

function setPhase(phase, message) {
  window.quest3TeleopState.phase = phase;
  setStatus(message);
}

function connectSocket() {
  socket = new WebSocket(`ws://${location.host}/ws`);
  socket.addEventListener("open", () => setStatus("Desktop connected. Press Start tracking."));
  socket.addEventListener("close", () => {
    setStatus("Desktop disconnected. Reconnecting...");
    window.setTimeout(connectSocket, 500);
  });
  socket.addEventListener("error", () => socket.close());
}

function packedHand(frame, inputSource) {
  if (!inputSource?.hand) {
    return { tracked: false };
  }
  const positions = [];
  const rotations = [];
  const radii = [];
  for (const name of webxrNames) {
    const jointSpace = inputSource.hand.get(name);
    const pose = jointSpace ? frame.getJointPose(jointSpace, referenceSpace) : null;
    if (!pose) {
      return { tracked: false };
    }
    const p = pose.transform.position;
    const q = pose.transform.orientation;
    positions.push(p.x, p.y, p.z);
    rotations.push(q.x, q.y, q.z, q.w);
    radii.push(Number.isFinite(pose.radius) ? pose.radius : 0.008);
  }
  return { tracked: true, positions_m: positions, rotations_xyzw: rotations, radii_m: radii };
}

function headPose(frame) {
  const viewer = frame.getViewerPose(referenceSpace);
  if (!viewer) return null;
  const p = viewer.transform.position;
  const q = viewer.transform.orientation;
  return { position_m: [p.x, p.y, p.z], rotation_xyzw: [q.x, q.y, q.z, q.w] };
}

async function selectTrackerFrameRate(activeSession) {
  if (
    typeof activeSession.updateTargetFrameRate !== "function"
    || !activeSession.supportedFrameRates
  ) return;
  const usable = Array.from(activeSession.supportedFrameRates)
    .filter((rate) => Number.isFinite(rate) && rate >= 60)
    .sort((left, right) => left - right);
  if (usable.length === 0) return;
  try {
    await activeSession.updateTargetFrameRate(usable[0]);
  } catch (_error) {
    // Frame-rate selection is an optional Quest Browser capability.
  }
}

function onFrame(timeMs, frame) {
  const activeSession = frame.session;
  activeSession.requestAnimationFrame(onFrame);
  gl.bindFramebuffer(gl.FRAMEBUFFER, activeSession.renderState.baseLayer.framebuffer);
  // Tracker-only mode renders one opaque black clear. There is no passthrough
  // composition and no scene geometry; the headset is being used as a sensor.
  gl.clearColor(0, 0, 0, 1);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const sources = { left: null, right: null };
  for (const source of activeSession.inputSources) {
    if (source.hand && (source.handedness === "left" || source.handedness === "right")) {
      sources[source.handedness] = source;
    }
  }
  socket.send(JSON.stringify({
    type: "quest3.hand_frame.v1",
    sequence: sequence++,
    // Browser-monotonic nanoseconds stay within JavaScript's exact integer
    // range. Desktop freshness uses its own monotonic receive timestamp.
    sender_timestamp_ns: Math.round(timeMs * 1e6),
    reference_space: "local",
    head: headPose(frame),
    hands: {
      left: packedHand(frame, sources.left),
      right: packedHand(frame, sources.right),
    },
  }));
}

async function startTracking() {
  if (session) {
    setPhase("tracking", "Tracking hands. Keep this session open.");
    return;
  }
  startButton.disabled = true;
  window.quest3TeleopState.error = "";
  try {
    gl = canvas.getContext("webgl", {
      alpha: false,
      antialias: false,
      depth: false,
      stencil: false,
      preserveDrawingBuffer: false,
    });
    if (!gl) throw new Error("WebGL is unavailable");

    // Quest Browser requires requestSession() to be invoked while the
    // transient activation created by the Start action is still live.  Do
    // not await WebGL setup first: that yields to the event loop and loses
    // the activation on current Quest Browser releases.
    setPhase("requesting_session", "Starting tracker-only hand session...");
    const requestedSession = navigator.xr.requestSession(
      "immersive-vr",
      { requiredFeatures: ["hand-tracking"] },
    );
    await gl.makeXRCompatible();
    session = await requestedSession;
    setPhase("preparing_session", "Preparing hand tracking...");
    session.updateRenderState({
      baseLayer: new XRWebGLLayer(session, gl, {
        alpha: false,
        antialias: false,
        depth: false,
        stencil: false,
        framebufferScaleFactor: 0.25,
      }),
    });
    referenceSpace = await session.requestReferenceSpace("local");
    // Some Quest Browser builds resolve updateTargetFrameRate only after XR
    // frames have begun. Never block hand tracking startup on this optional
    // power optimization.
    void selectTrackerFrameRate(session);
    session.addEventListener("end", () => {
      session = null;
      startButton.disabled = false;
      startButton.textContent = "Start tracking";
      setPhase("ended", "Tracking stopped.");
    });
    setPhase("tracking", "Tracking hands. Keep this session open.");
    session.requestAnimationFrame(onFrame);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    window.quest3TeleopState.error = message;
    setPhase("error", `Could not start: ${message}`);
    startButton.disabled = false;
  }
}

async function stopTracking() {
  const activeSession = session;
  if (!activeSession) {
    setPhase("ended", "Tracking stopped.");
    return;
  }
  setPhase("stopping", "Stopping hand tracking...");
  try {
    await activeSession.end();
  } catch (_error) {
    // The session may already be ending because Browser is closing.
  } finally {
    if (session === activeSession) session = null;
    startButton.disabled = false;
    startButton.textContent = "Start tracking";
    setPhase("ended", "Tracking stopped.");
  }
}

async function initialize() {
  connectSocket();
  if (!window.isSecureContext || !navigator.xr) {
    setStatus("WebXR unavailable. Confirm the URL is http://localhost through USB.");
    return;
  }
  const supported = await navigator.xr.isSessionSupported("immersive-vr");
  if (!supported) {
    setStatus("This browser does not report immersive VR hand tracking support.");
    return;
  }
  startButton.disabled = false;
  startButton.textContent = "Start tracking";
  startButton.addEventListener("click", startTracking);
  window.quest3StartTracking = startTracking;
  window.quest3StopTracking = stopTracking;
  setPhase("ready", "Desktop connected. Ready to start tracking.");
}

initialize().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  window.quest3TeleopState.error = message;
  setPhase("error", `Initialization failed: ${message}`);
});
