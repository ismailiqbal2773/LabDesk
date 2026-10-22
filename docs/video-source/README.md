# LabDesk demo source

30 seconds, 1080×1080, 30fps, silent on-screen text. Built with HyperFrames 0.8.185 from genuine app screenshots and fictional records. No school/student data.

Requires Node.js 22+ and HyperFrames' browser/FFmpeg prerequisites for video authoring only; the LabDesk application itself still needs only Python.

Run `npm run dev` to edit the composition in Studio, `npm run check` to validate, and `npm run render -- --quality delivery --output labdesk-demo.mp4` to encode. See SPEC.md, STORYBOARD.md and REVIEW.md for production choices and verification limits.
