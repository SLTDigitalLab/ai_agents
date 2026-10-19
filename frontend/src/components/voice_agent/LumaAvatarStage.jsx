import React, { Component, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Environment, useGLTF } from '@react-three/drei';
import { AnimationMixer, Box3, LoopRepeat, Vector3 } from 'three';
import { clone as cloneSkinned } from 'three/examples/jsm/utils/SkeletonUtils.js';
import { buildVisemeTimeline, sampleVisemes } from './LumaLipSync';

// Names verified in Luma_2.1_Rig_Verified.glb. They are deliberately not
// inferred from the Blender controls which may not be present in a GLB export.
const CONVERSATION_CLIP = 'Luma_Natural_Conversation_Final';
const IDLE_CLIP = 'Luma_Natural_Idle';
// Keep this enabled only while validating the imported GLB's rest pose.
// It prevents animation from obscuring global-orientation diagnosis.
const STATIC_ORIENTATION_TEST = false;


class AvatarErrorBoundary extends Component {
    state = { failed: false };
    static getDerivedStateFromError() { return { failed: true }; }
    componentDidCatch(error) { console.error('Luma avatar failed to load:', error); }
    render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

function StageCamera({ metrics }) {
    const { camera, size } = useThree();
    useFrame(() => {
        if (!metrics) return;
        const verticalFov = (camera.fov * Math.PI) / 180;
        const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * (size.width / size.height));
        // Keep the complete body inside ~84% of the viewport height, with a
        // matching horizontal calculation for smaller screens.
        const verticalDistance = metrics.height / (2 * Math.tan(verticalFov / 2) * 0.84);
        const horizontalDistance = metrics.width / (2 * Math.tan(horizontalFov / 2) * 0.9);
        const distance = Math.max(verticalDistance, horizontalDistance, 1.8);
        // Use a level, front-facing character camera.  An elevated camera
        // looks down onto Luma's feet and can make an upright model look as
        // though it is lying down or facing backwards.
        camera.position.set(0, metrics.centerY, distance);
        camera.lookAt(0, metrics.centerY, 0);
    });
    return null;
}

function LumaModel({ isSpeaking, speech, onLoaded, onMetrics }) {
    const { scene, animations } = useGLTF('/models/luma.glb');
    const { model, metrics } = useMemo(() => {
        // A normal Object3D.clone leaves a SkinnedMesh bound to bones in the
        // source scene. Clone the entire skeleton hierarchy so the legs, arms,
        // and hands stay correctly bound to their own cloned Rigify bones.
        const clone = cloneSkinned(scene);
        // Blender exported these Rigify UI widgets as separate visible roots.
        // They are not part of Luma's renderable body and corrupt camera bounds.
        clone.children
            .filter((node) => node.name.startsWith('WGT-rig-') || node.name === 'metarig')
            .forEach((node) => clone.remove(node));
        // The raw full-body mesh is exported as a sibling of the Rigify `root`
        // bone. That misses the root's -90° X Blender→Three conversion and
        // makes the body appear feet-first. Parent it to that existing root so
        // the entire body receives the same upright axis transform as its
        // skinned face, eyes, mouth and facial overlays.
        // Diagnostic render confirmed that the GLB's existing Rigify axis
        // conversion makes Y vertical, but leaves its vertical direction
        // inverted. Rotate the *single avatar container* around the camera
        // axis: this puts hair above feet without turning Luma's chest away
        // from the normal front-facing camera or changing any bones.
        // Render the verified character mesh and its actual facial overlays.
        // This final GLB exports its 149,952-vertex full-body mesh as the
        // `Luma_AutoWeights_PreCleanup` node (geometry name: `material`). The
        // similarly named `material` node is the separate integrated face
        // overlay, not the body. Keep both renderable components together.
        // The aperture cutter is exporter utility geometry and must not render.
        const nonDisplayMeshes = [];
        clone.traverse((node) => {
            if (!node.isMesh) return;
            if (node.name === 'Luma_Mouth_Aperture_Cutter') {
                nonDisplayMeshes.push(node);
                return;
            }
            node.visible = true;
            node.frustumCulled = false;
        });
        nonDisplayMeshes.forEach((node) => node.parent?.remove(node));
        clone.updateMatrixWorld(true);
        const bounds = new Box3().setFromObject(clone, true);
        if (bounds.isEmpty()) throw new Error('The Luma full-body mesh could not be located in the GLB.');
        const dimensions = bounds.getSize(new Vector3());
        // Align the actual lowest visible vertex to the scene floor (y=0),
        // never the GLB's arbitrary origin.
        clone.position.y = -bounds.min.y;
        clone.updateMatrixWorld(true);
        return {
            model: clone,
            metrics: {
                height: dimensions.y,
                width: dimensions.x,
                depth: dimensions.z,
                centerY: dimensions.y * 0.52,
            },
        };
    }, [scene]);
    const meshes = useRef([]);
    const mixer = useRef(null);
    const idleAction = useRef(null);
    const conversationAction = useRef(null);
    const speakingRef = useRef(isSpeaking);
    const nextBlinkAt = useRef(0);
    const morphValues = useRef({});
    const visemeTimeline = useMemo(
        () => buildVisemeTimeline(speech?.text, speech?.duration),
        [speech?.revision, speech?.text, speech?.duration],
    );

    useEffect(() => {
        speakingRef.current = isSpeaking;
    }, [isSpeaking]);

    useEffect(() => {
        const found = [];
        model.traverse((node) => {
            if (node.isMesh && node.morphTargetDictionary && node.morphTargetInfluences) found.push(node);
        });
        meshes.current = found;
        // Use the supplied conversational motion. Eye tracks are intentionally
        // removed so pupils never move; blink is driven only by eyelid shapes.
        const withoutEyeMotion = (clip) => {
            const bodyOnlyClip = clip.clone();
            bodyOnlyClip.tracks = bodyOnlyClip.tracks.filter((track) => {
                const isEyeTrack = /(^|[/.])(eye|eyes|master_eye|mch-eye|org-eye)/i.test(track.name);
                // Preserve the root's exported static axis conversion. The
                // clips include global root tracks which otherwise overturn it;
                // all child body/hand/head animation tracks continue playing.
                const isGlobalRootTrack = /^root\.(position|quaternion|scale)$/.test(track.name);
                const isMorphTrack = track.name.includes('morphTargetInfluences');
                return !isEyeTrack && !isGlobalRootTrack && !isMorphTrack;
            });
            return bodyOnlyClip;
        };
        // This GLB has one merged conversation clip. Blender's subtle idle
        // layer is baked into its torso tracks, so derive a quiet looping idle
        // from those tracks instead of waiting for a separate, absent clip.
        // Arms, hands, face morphs and eyelids are excluded from this layer.
        const deriveIdle = (sourceClip) => {
            const derived = sourceClip.clone();
            derived.name = 'Luma_Derived_Idle';
            derived.tracks = derived.tracks.filter((track) => (
                !track.name.includes('morphTargetInfluences')
                && !/(upper_arm|forearm|hand|finger|thigh|shin|foot|toe)/i.test(track.name)
            ));
            return derived;
        };
        const clip = animations.find((item) => item.name === CONVERSATION_CLIP);
        const authoredIdleClip = animations.find((item) => item.name === IDLE_CLIP);
        const idleClip = authoredIdleClip
            ? withoutEyeMotion(authoredIdleClip)
            : clip ? deriveIdle(withoutEyeMotion(clip)) : null;
        if (!STATIC_ORIENTATION_TEST && (clip || idleClip)) {
            mixer.current = new AnimationMixer(model);
        }
        if (!STATIC_ORIENTATION_TEST && idleClip) {
            idleAction.current = mixer.current.clipAction(idleClip);
            idleAction.current.setLoop(LoopRepeat, Infinity);
            idleAction.current.timeScale = 0.55;
            if (!speakingRef.current) idleAction.current.play();
        }
        if (!STATIC_ORIENTATION_TEST && clip) {
            const bodyOnlyClip = withoutEyeMotion(clip);
            conversationAction.current = mixer.current.clipAction(bodyOnlyClip);
            conversationAction.current.setLoop(LoopRepeat, Infinity);
            if (speakingRef.current) conversationAction.current.play();
        }
        onLoaded?.(found.length > 0 && (STATIC_ORIENTATION_TEST || Boolean(clip || idleClip)));
        return () => {
            mixer.current?.stopAllAction();
            onLoaded?.(false);
        };
    }, [animations, model, onLoaded]);

    useEffect(() => onMetrics?.(metrics), [metrics, onMetrics]);

    useEffect(() => {
        if (STATIC_ORIENTATION_TEST || !isSpeaking) return;
        // One pass preserves the authored stillness → gesture → settle rhythm
        // and avoids a repetitive generated hand-motion loop.
        idleAction.current?.fadeOut(0.18);
        conversationAction.current?.reset().fadeIn(0.18).play();
    }, [isSpeaking]);

    useEffect(() => {
        if (STATIC_ORIENTATION_TEST || isSpeaking) return;
        conversationAction.current?.fadeOut(0.18);
        idleAction.current?.reset().fadeIn(0.18).play();
    }, [isSpeaking]);

    useFrame((state, delta) => {
        if (STATIC_ORIENTATION_TEST) return;
        mixer.current?.update(delta);
        const now = state.clock.elapsedTime;
        if (!nextBlinkAt.current) nextBlinkAt.current = now + 3.8 + Math.random() * 2.5;
        const blink = now >= nextBlinkAt.current && now < nextBlinkAt.current + 0.12;
        if (now >= nextBlinkAt.current + 0.12) nextBlinkAt.current = now + 3.5 + Math.random() * 3.5;

        meshes.current.forEach((mesh) => {
            if (!mesh.name.startsWith('Luma_Eyelid')) return;
            const { morphTargetDictionary: dictionary, morphTargetInfluences: influences } = mesh;
            const closedIndex = dictionary.Closed;
            if (closedIndex === undefined) return;
            const target = blink ? 1 : 0;
            influences[closedIndex] += (target - influences[closedIndex]) * 0.28;
        });

        const audioTime = speech?.getTime?.();
        const lipSyncActive = Boolean(speech?.active && Number.isFinite(audioTime));
        const targets = lipSyncActive ? sampleVisemes(visemeTimeline, audioTime) : {};

        // Dynamic lip-sync: the response text becomes a temporary viseme plan
        // and this follows the real scheduled audio clock.  The GLB's fixed
        // sentence morph tracks are intentionally filtered out above.
        meshes.current.forEach((mesh) => {
            const { morphTargetDictionary: dictionary, morphTargetInfluences: influences } = mesh;
            // The Web GLB's visible face is named
            // Luma_Integrated_Mouth_Face001 (with two companion meshes).
            // `material` is only its parent Group, so filtering by that name
            // meant the live face never received any viseme influences. Drive
            // every visible mesh that actually exposes the exported speech
            // morphs, resolving every index from its own dictionary.
            const hasSpeechMorphs = dictionary
                && ['Viseme_A', 'Viseme_E', 'Viseme_I', 'Viseme_O', 'Viseme_U', 'Viseme_MBP']
                    .some((name) => dictionary[name] !== undefined);
            if (!hasSpeechMorphs) return;
            Object.entries(dictionary).forEach(([name, index]) => {
                const target = name === 'Rounded_Vowel_Recess'
                    ? Math.max(targets.Viseme_O || 0, (targets.Viseme_U || 0) * 0.7)
                    : (targets[name] || 0);
                const key = `${mesh.uuid}:${name}`;
                const current = morphValues.current[key] || 0;
                // A short, frame-rate independent visual easing removes harsh
                // phoneme-to-phoneme snaps without delaying the audio-driven
                // timeline or changing TTS speed.
                const smoothing = 1 - Math.exp(-delta * 11.5);
                const smoothed = current + (target - current) * smoothing;
                morphValues.current[key] = smoothed;
                influences[index] = smoothed;
            });
        });

    });

    return <primitive object={model} dispose={null} />;
}

const FallbackAvatar = () => (
    <div className="h-full w-full flex items-center justify-center rounded-[2rem] bg-gradient-to-b from-cyan-950 via-slate-900 to-slate-950 text-center text-cyan-100">
        <div><div className="mx-auto mb-3 h-16 w-16 rounded-full border border-cyan-300/40 bg-cyan-400/10" /><p className="text-sm">Avatar unavailable</p></div>
    </div>
);

export default function LumaAvatarStage({ isSpeaking, speech, onLoaded }) {
    const [metrics, setMetrics] = useState(null);
    return (
        <div className="relative h-[min(58vh,560px)] w-full max-w-4xl shrink-0">
            <AvatarErrorBoundary fallback={<FallbackAvatar />}>
                <Canvas gl={{ alpha: true }} camera={{ position: [0, 1, 5], fov: 29, near: 0.01, far: 100 }} dpr={[1, 2]}>
                    <StageCamera metrics={metrics} />
                    <ambientLight intensity={1.35} />
                    <hemisphereLight args={['#c5f5ff', '#07111f', 1.4]} />
                    <spotLight position={[2.5, 6, 4]} intensity={3.1} angle={0.42} penumbra={0.8} castShadow />
                    <pointLight position={[-3, 2.5, 1]} intensity={9} color="#45d8ff" distance={7} />
                    <Suspense fallback={null}>
                        <LumaModel isSpeaking={isSpeaking} speech={speech} onLoaded={onLoaded} onMetrics={setMetrics} />
                        <Environment preset="city" />
                    </Suspense>
                </Canvas>
            </AvatarErrorBoundary>
        </div>
    );
}

useGLTF.preload('/models/luma.glb');
