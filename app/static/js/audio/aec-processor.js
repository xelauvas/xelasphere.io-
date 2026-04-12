/**
 * AEC Processor — AudioWorklet that subtracts TTS reference signal from mic input.
 * Not true acoustic echo cancellation (no delay estimation, no NLMS adaptation),
 * but a simple reference-subtract that complements browser-level echoCancellation.
 *
 * Reference samples are pushed via port.postMessage({type: 'reference', samples}).
 * A ring buffer holds up to 300ms of reference audio (~4800 samples at 16kHz).
 */
class AECProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.referenceBuffer = [];
        this.maxRef = 4800; // 300ms at 16kHz
        this.port.onmessage = (e) => {
            if (e.data.type === 'reference' && Array.isArray(e.data.samples)) {
                this.referenceBuffer.push(...e.data.samples);
                if (this.referenceBuffer.length > this.maxRef) {
                    this.referenceBuffer = this.referenceBuffer.slice(-this.maxRef);
                }
            } else if (e.data.type === 'clear') {
                this.referenceBuffer = [];
            }
        };
    }

    process(inputs, outputs) {
        const input = inputs[0] && inputs[0][0];
        const output = outputs[0] && outputs[0][0];
        if (!input || !output) return true;

        const refLen = this.referenceBuffer.length;
        const blockSize = input.length;

        for (let i = 0; i < blockSize; i++) {
            // Take the most recent reference samples aligned to input tail
            const refIdx = refLen - blockSize + i;
            const ref = refIdx >= 0 ? this.referenceBuffer[refIdx] : 0;
            // Simple reference subtraction with 0.85 gain
            output[i] = input[i] - ref * 0.85;
        }

        // Consume the reference samples we just used
        if (refLen >= blockSize) {
            this.referenceBuffer = this.referenceBuffer.slice(blockSize);
        }
        return true;
    }
}

registerProcessor('aec-processor', AECProcessor);
