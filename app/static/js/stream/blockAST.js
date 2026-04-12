export function createBlockAST() {
    const blocks = [];
    let liveBlock = null;

    return {
        blocks() {
            return blocks;
        },
        getLive() {
            return liveBlock;
        },
        startBlock({ id, type, meta }) {
            if (liveBlock) {
                liveBlock.frozen = true;
                liveBlock = null;
            }
            liveBlock = { id, type, meta: { ...meta }, text: '', frozen: false, el: null };
            blocks.push(liveBlock);
            return liveBlock;
        },
        appendToLive(text) {
            if (!liveBlock) throw new Error('no live block');
            liveBlock.text += text;
        },
        endBlock() {
            if (!liveBlock) return;
            liveBlock.frozen = true;
            liveBlock = null;
        },
    };
}
