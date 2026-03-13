/**
 * Recipe Detection & Rendering Module
 * ════════════════════════════════════════════════════════════════════════════
 *
 * This module provides conservative recipe detection with weighted scoring,
 * multi-recipe parsing, and styled card rendering.
 *
 * Detection Philosophy:
 * - Conservative by default: prefer false negatives over false positives
 * - Never treat fenced code blocks as recipes
 * - Weighted scoring that scales with content length
 * - Explicit measurements can override low keyword scores
 *
 * Backend Alignment (conversation_context.py):
 * - Backend uses similar patterns: "ingredients:", "instructions:", measurement units
 * - Backend confidence weights: recipe keyword (0.95), ingredients (0.9), instructions (0.8)
 * - Frontend weighted score roughly maps to backend confidence thresholds
 * - Keep patterns aligned when updating either side
 */

// ════════════════════════════════════════════════════════════════════════════
// CONFIGURATION
// ════════════════════════════════════════════════════════════════════════════

const RecipeConfig = {
    // Enable dev logging (set to false for production)
    DEV_LOGGING: false,

    // Minimum weighted score thresholds (scales with content length)
    THRESHOLDS: {
        SHORT: 12,    // < 500 chars: lower threshold for concise recipes
        MEDIUM: 18,   // 500-1500 chars: standard threshold
        LONG: 25      // > 1500 chars: higher threshold to avoid false positives
    },

    // Explicit measurements override: if found, reduce threshold
    MEASUREMENT_OVERRIDE_BONUS: 8,

    // Weights for different signal types
    WEIGHTS: {
        // Section headings (strongest signals)
        SECTION_INGREDIENTS: 8,
        SECTION_INSTRUCTIONS: 6,
        SECTION_DIRECTIONS: 6,
        SECTION_STEPS: 5,
        SECTION_METHOD: 4,
        SECTION_PREPARATION: 4,

        // Cooking verbs (medium signals)
        VERB_COOK: 2,
        VERB_BAKE: 2,
        VERB_FRY: 2,
        VERB_BOIL: 2,
        VERB_SIMMER: 2,
        VERB_ROAST: 2,
        VERB_GRILL: 2,
        VERB_SAUTE: 2,
        VERB_STIR: 1.5,
        VERB_MIX: 1,
        VERB_CHOP: 1.5,
        VERB_SLICE: 1.5,
        VERB_DICE: 1.5,
        VERB_MINCE: 1.5,
        VERB_PREHEAT: 2,
        VERB_MARINATE: 2,
        VERB_SEASON: 1.5,
        VERB_WHISK: 1.5,
        VERB_BLEND: 1.5,
        VERB_FOLD: 1.5,
        VERB_KNEAD: 2,

        // Equipment (weak signals - common in non-recipe contexts)
        EQUIP_OVEN: 1.5,
        EQUIP_PAN: 1,
        EQUIP_POT: 1,
        EQUIP_BOWL: 0.5,
        EQUIP_SKILLET: 1.5,

        // Time/serving phrases (medium signals)
        TIME_MINUTES: 1.5,
        TIME_HOURS: 1.5,
        SERVINGS: 2,

        // Measurement units (strong signals when with numbers)
        MEASURE_CUP: 2,
        MEASURE_TBSP: 2.5,
        MEASURE_TSP: 2.5,
        MEASURE_OZ: 1.5,
        MEASURE_LB: 1.5,
        MEASURE_GRAM: 2,
        MEASURE_ML: 1.5,
        MEASURE_CLOVE: 2,
        MEASURE_SLICE: 1,
        MEASURE_PIECE: 1
    }
};

// ════════════════════════════════════════════════════════════════════════════
// DETECTION HELPERS
// ════════════════════════════════════════════════════════════════════════════

/**
 * Calculate weighted recipe score for content
 * @param {string} content - The text content to analyze
 * @returns {object} Score breakdown with total and individual signals
 */
function calculateRecipeScore(content) {
    const lowerContent = content.toLowerCase();
    const W = RecipeConfig.WEIGHTS;

    const signals = {
        sections: [],
        verbs: [],
        equipment: [],
        timings: [],
        measurements: [],
        total: 0
    };

    // Section headings (strongest signals)
    if (/\bingredients?\s*[:\-]/i.test(content)) {
        signals.sections.push({ name: 'ingredients', weight: W.SECTION_INGREDIENTS });
    }
    if (/\binstructions?\s*[:\-]/i.test(content)) {
        signals.sections.push({ name: 'instructions', weight: W.SECTION_INSTRUCTIONS });
    }
    if (/\bdirections?\s*[:\-]/i.test(content)) {
        signals.sections.push({ name: 'directions', weight: W.SECTION_DIRECTIONS });
    }
    if (/\bsteps?\s*[:\-]/i.test(content)) {
        signals.sections.push({ name: 'steps', weight: W.SECTION_STEPS });
    }
    if (/\bmethod\s*[:\-]/i.test(content)) {
        signals.sections.push({ name: 'method', weight: W.SECTION_METHOD });
    }
    if (/\bpreparation\s*[:\-]/i.test(content)) {
        signals.sections.push({ name: 'preparation', weight: W.SECTION_PREPARATION });
    }

    // Cooking verbs
    const verbPatterns = [
        [/\bcook(?:ed|ing|s)?\b/i, 'cook', W.VERB_COOK],
        [/\bbak(?:e|ed|ing|es)\b/i, 'bake', W.VERB_BAKE],
        [/\bfr(?:y|ied|ying|ies)\b/i, 'fry', W.VERB_FRY],
        [/\bboil(?:ed|ing|s)?\b/i, 'boil', W.VERB_BOIL],
        [/\bsimmer(?:ed|ing|s)?\b/i, 'simmer', W.VERB_SIMMER],
        [/\broast(?:ed|ing|s)?\b/i, 'roast', W.VERB_ROAST],
        [/\bgrill(?:ed|ing|s)?\b/i, 'grill', W.VERB_GRILL],
        [/\bsaut[eé](?:ed|ing|s)?\b/i, 'saute', W.VERB_SAUTE],
        [/\bstir(?:red|ring|s)?\b/i, 'stir', W.VERB_STIR],
        [/\bmix(?:ed|ing|es)?\b/i, 'mix', W.VERB_MIX],
        [/\bchop(?:ped|ping|s)?\b/i, 'chop', W.VERB_CHOP],
        [/\bslic(?:e|ed|ing|es)\b/i, 'slice', W.VERB_SLICE],
        [/\bdic(?:e|ed|ing|es)\b/i, 'dice', W.VERB_DICE],
        [/\bminc(?:e|ed|ing|es)\b/i, 'mince', W.VERB_MINCE],
        [/\bpreheat(?:ed|ing|s)?\b/i, 'preheat', W.VERB_PREHEAT],
        [/\bmarinat(?:e|ed|ing|es)\b/i, 'marinate', W.VERB_MARINATE],
        [/\bseason(?:ed|ing|s)?\b/i, 'season', W.VERB_SEASON],
        [/\bwhisk(?:ed|ing|s)?\b/i, 'whisk', W.VERB_WHISK],
        [/\bblend(?:ed|ing|s)?\b/i, 'blend', W.VERB_BLEND],
        [/\bfold(?:ed|ing|s)?\b/i, 'fold', W.VERB_FOLD],
        [/\bknead(?:ed|ing|s)?\b/i, 'knead', W.VERB_KNEAD]
    ];

    for (const [pattern, name, weight] of verbPatterns) {
        if (pattern.test(content)) {
            signals.verbs.push({ name, weight });
        }
    }

    // Equipment
    if (/\boven\b/i.test(content)) signals.equipment.push({ name: 'oven', weight: W.EQUIP_OVEN });
    if (/\bpan\b/i.test(content)) signals.equipment.push({ name: 'pan', weight: W.EQUIP_PAN });
    if (/\bpot\b/i.test(content)) signals.equipment.push({ name: 'pot', weight: W.EQUIP_POT });
    if (/\bbowl\b/i.test(content)) signals.equipment.push({ name: 'bowl', weight: W.EQUIP_BOWL });
    if (/\bskillet\b/i.test(content)) signals.equipment.push({ name: 'skillet', weight: W.EQUIP_SKILLET });

    // Time/serving phrases
    if (/\d+\s*(?:min(?:ute)?s?)\b/i.test(content)) {
        signals.timings.push({ name: 'minutes', weight: W.TIME_MINUTES });
    }
    if (/\d+\s*(?:hours?|hrs?)\b/i.test(content)) {
        signals.timings.push({ name: 'hours', weight: W.TIME_HOURS });
    }
    if (/\bservings?\b|\bserves?\s+\d+/i.test(content)) {
        signals.timings.push({ name: 'servings', weight: W.SERVINGS });
    }

    // Measurement units (with numbers - stronger signal)
    const measurePatterns = [
        [/\d+\s*(?:cups?)\b/i, 'cup', W.MEASURE_CUP],
        [/\d+\s*(?:tbsp|tablespoons?)\b/i, 'tbsp', W.MEASURE_TBSP],
        [/\d+\s*(?:tsp|teaspoons?)\b/i, 'tsp', W.MEASURE_TSP],
        [/\d+\s*(?:oz|ounces?)\b/i, 'oz', W.MEASURE_OZ],
        [/\d+\s*(?:lbs?|pounds?)\b/i, 'lb', W.MEASURE_LB],
        [/\d+\s*(?:g|grams?)\b/i, 'gram', W.MEASURE_GRAM],
        [/\d+\s*(?:ml|milliliters?)\b/i, 'ml', W.MEASURE_ML],
        [/\d+\s*(?:cloves?)\b/i, 'clove', W.MEASURE_CLOVE],
        [/\d+\s*(?:slices?)\b/i, 'slice', W.MEASURE_SLICE],
        [/\d+\s*(?:pieces?)\b/i, 'piece', W.MEASURE_PIECE]
    ];

    for (const [pattern, name, weight] of measurePatterns) {
        if (pattern.test(content)) {
            signals.measurements.push({ name, weight });
        }
    }

    // Calculate total
    signals.total = [
        ...signals.sections,
        ...signals.verbs,
        ...signals.equipment,
        ...signals.timings,
        ...signals.measurements
    ].reduce((sum, s) => sum + s.weight, 0);

    return signals;
}

/**
 * Get threshold based on content length
 * @param {number} length - Content length in characters
 * @returns {number} Threshold value
 */
function getThresholdForLength(length) {
    const T = RecipeConfig.THRESHOLDS;
    if (length < 500) return T.SHORT;
    if (length < 1500) return T.MEDIUM;
    return T.LONG;
}

/**
 * Check if content was from a fenced code block
 * @param {string} lang - Language identifier from code block
 * @param {object} metadata - Optional metadata from extraction
 * @returns {boolean} True if this was a fenced code block
 */
function isFromFencedCodeBlock(lang, metadata = {}) {
    // If explicitly marked with a programming language, it's code
    const codeLanguages = [
        'javascript', 'js', 'typescript', 'ts', 'python', 'py', 'java', 'c', 'cpp',
        'csharp', 'cs', 'go', 'rust', 'ruby', 'php', 'swift', 'kotlin', 'scala',
        'html', 'css', 'scss', 'sass', 'less', 'sql', 'graphql', 'json', 'yaml',
        'yml', 'xml', 'markdown', 'md', 'bash', 'sh', 'shell', 'powershell', 'ps1',
        'dockerfile', 'makefile', 'cmake', 'gradle', 'maven', 'r', 'matlab',
        'perl', 'lua', 'vim', 'latex', 'tex', 'diff', 'assembly', 'asm'
    ];

    if (lang && codeLanguages.includes(lang.toLowerCase())) {
        return true;
    }

    // Check metadata flag if available
    if (metadata.wasFenced === true) {
        return true;
    }

    return false;
}

// ════════════════════════════════════════════════════════════════════════════
// MAIN DETECTION FUNCTION
// ════════════════════════════════════════════════════════════════════════════

/**
 * Determine if content is a recipe
 * @param {string} content - The text content to analyze
 * @param {string} lang - Language identifier (from code block)
 * @param {object} options - Additional options
 * @param {boolean} options.wasFenced - True if content was in a fenced code block
 * @returns {boolean} True if content should be rendered as a recipe
 */
function isRecipe(content, lang = null, options = {}) {
    // RULE 1: Never treat fenced code blocks as recipes
    if (isFromFencedCodeBlock(lang, options)) {
        if (RecipeConfig.DEV_LOGGING) {
            console.log('[RecipeDetector] Skipped: fenced code block', { lang });
        }
        return false;
    }

    // RULE 2: Skip if explicitly marked as plaintext code (user intent)
    // Allow 'text' and empty lang to proceed
    if (lang && lang !== 'plaintext' && lang !== 'text' && lang !== '') {
        if (RecipeConfig.DEV_LOGGING) {
            console.log('[RecipeDetector] Skipped: has language', { lang });
        }
        return false;
    }

    // RULE 3: Must have minimum content
    if (!content || content.length < 50) {
        return false;
    }

    // Calculate weighted score
    const score = calculateRecipeScore(content);

    // Get threshold based on content length
    let threshold = getThresholdForLength(content.length);

    // RULE 4: Must have at least one section heading (ingredients OR instructions)
    const hasRequiredSection = score.sections.some(s =>
        ['ingredients', 'instructions', 'directions', 'steps'].includes(s.name)
    );

    if (!hasRequiredSection) {
        if (RecipeConfig.DEV_LOGGING) {
            console.log('[RecipeDetector] Skipped: no section headings', {
                score: score.total,
                threshold
            });
        }
        return false;
    }

    // RULE 5: Measurement override - if we have explicit measurements, lower threshold
    if (score.measurements.length >= 2) {
        threshold -= RecipeConfig.MEASUREMENT_OVERRIDE_BONUS;
    }

    // Final decision
    const isRecipeContent = score.total >= threshold;

    // Dev logging with full breakdown
    if (RecipeConfig.DEV_LOGGING) {
        console.log('[RecipeDetector] Analysis:', {
            isRecipe: isRecipeContent,
            score: score.total,
            threshold,
            contentLength: content.length,
            breakdown: {
                sections: score.sections.map(s => `${s.name}(${s.weight})`).join(', '),
                verbs: score.verbs.map(s => `${s.name}(${s.weight})`).join(', '),
                equipment: score.equipment.map(s => `${s.name}(${s.weight})`).join(', '),
                timings: score.timings.map(s => `${s.name}(${s.weight})`).join(', '),
                measurements: score.measurements.map(s => `${s.name}(${s.weight})`).join(', ')
            }
        });
    }

    // TODO: Hook for telemetry - record classification for false positive/negative analysis
    // if (window.RecipeTelemetry) {
    //     window.RecipeTelemetry.record({ content, score, threshold, result: isRecipeContent });
    // }

    return isRecipeContent;
}

// ════════════════════════════════════════════════════════════════════════════
// MULTI-RECIPE SPLITTING
// ════════════════════════════════════════════════════════════════════════════

/**
 * Split content into individual recipes
 * Handles multiple formats: separators, headers, and multiple ingredient blocks
 * @param {string} content - Full content potentially containing multiple recipes
 * @returns {object} { intro: string, recipes: string[] }
 */
function splitRecipes(content) {
    const result = {
        intro: '',
        recipes: []
    };

    // Extract intro text (text before first recipe header or ingredients)
    const introMatch = content.match(/^([\s\S]*?)(?=^#{1,3}\s|^\*\*[A-Z]|\bingredients?\s*[:\-])/im);
    if (introMatch && introMatch[1]) {
        const intro = introMatch[1]
            .replace(/[\u{1F300}-\u{1F9FF}]/gu, '')  // Remove emojis
            .replace(/[\u{2600}-\u{26FF}]/gu, '')
            .replace(/[*_]+/g, '')  // Remove markdown
            .trim();
        if (intro.length > 3 && intro.length < 200) {
            result.intro = intro;
        }
    }

    // Method 1: Explicit === separator
    if (content.includes('===')) {
        result.recipes = content.split(/\n===+\n/).map(r => r.trim()).filter(r => r.length > 0);
        if (result.recipes.length > 1) {
            return result;
        }
    }

    // Method 2: Recipe emoji headers (## 🍳, ### 🥘, etc.)
    const emojiHeaderPattern = /(?=^#{1,3}\s*[\u{1F300}-\u{1F9FF}])/gmu;
    let parts = content.split(emojiHeaderPattern).filter(r => r.trim().length > 0);
    if (parts.length > 1) {
        result.recipes = parts.map(r => r.trim());
        return result;
    }

    // Method 3: Numbered recipe headers (### 1. 🍳, ## 2., etc.)
    const numberedPattern = /(?=^#{1,3}\s*\d+\.?\s*)/gm;
    parts = content.split(numberedPattern).filter(r => r.trim().length > 0);
    if (parts.length > 1) {
        // Verify these look like recipe sections (not just numbered lists)
        const looksLikeRecipes = parts.filter(p =>
            /ingredients?|instructions?|directions?/i.test(p)
        ).length >= parts.length * 0.5;

        if (looksLikeRecipes) {
            result.recipes = parts.map(r => r.trim());
            return result;
        }
    }

    // Method 4: Multiple "Ingredients:" blocks (recipes without clear headers)
    const ingredientMatches = [...content.matchAll(/\bingredients?\s*[:\-]/gi)];
    if (ingredientMatches.length > 1) {
        // Split at each "Ingredients:" occurrence
        const indices = ingredientMatches.map(m => m.index);
        const splitParts = [];

        for (let i = 0; i < indices.length; i++) {
            const start = indices[i];
            const end = i < indices.length - 1 ? indices[i + 1] : content.length;

            // Look backwards for a title/header
            let recipeStart = start;
            const beforeIngredients = content.substring(Math.max(0, start - 200), start);
            const headerMatch = beforeIngredients.match(/(?:^|\n)(#{1,3}\s*.+|\*\*.+\*\*)(?:\n|$)/);
            if (headerMatch) {
                recipeStart = start - (beforeIngredients.length - headerMatch.index);
            }

            splitParts.push(content.substring(recipeStart, end).trim());
        }

        if (splitParts.length > 1) {
            result.recipes = splitParts;
            return result;
        }
    }

    // Single recipe
    result.recipes = [content];
    return result;
}

// ════════════════════════════════════════════════════════════════════════════
// RECIPE PARSING
// ════════════════════════════════════════════════════════════════════════════

/**
 * Parse recipe content into structured data
 * @param {string} content - Raw recipe text
 * @returns {object} Parsed recipe object
 */
function parseRecipe(content) {
    const lines = content.split('\n');

    const recipe = {
        title: '',
        time: '',
        servings: '',
        difficulty: '',
        ingredients: [],
        instructions: [],
        tip: ''
    };

    let section = 'title';

    // Debug logging
    const DEBUG_RECIPE = true;
    if (DEBUG_RECIPE) console.log('[RecipeParser] Starting parse, lines:', lines.length);

    for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;
        if (DEBUG_RECIPE && i < 15) console.log(`[RecipeParser] Line ${i}: section=${section}, line="${line.substring(0, 50)}..."`);

        // Detect title from ## header or first meaningful line
        if (section === 'title' && recipe.title === '') {
            // Match markdown headers
            const headerMatch = line.match(/^#{1,3}\s*(.+)$/);
            if (headerMatch) {
                // Clean up the title - remove numbers, emojis, markdown
                let title = headerMatch[1]
                    .replace(/^[\d]+\.?\s*/, '')  // Remove leading "2. " or "2 "
                    .replace(/[\u{1F300}-\u{1F9FF}]/gu, '')  // Remove all emojis
                    .replace(/[\u{2600}-\u{26FF}]/gu, '')  // Remove misc symbols
                    .replace(/[#*_]+/g, '')  // Remove markdown formatting
                    .replace(/^\s+|\s+$/g, '')  // Trim
                    .replace(/\s+/g, ' ');  // Normalize spaces

                // Skip placeholder titles - these indicate AI didn't follow format
                if (/^(recipe name|recipe title|\[.*\]|your recipe|example)$/i.test(title)) {
                    continue; // Don't use placeholder as title, keep looking
                }

                // Skip section headers - these are not titles
                if (/^(ingredients?|instructions?|directions?|steps?|method|preparation)$/i.test(title)) {
                    section = 'ingredients'; // Switch to ingredients section
                    continue;
                }

                recipe.title = title;
                continue;
            }
            // Check for bold title like **Avocado Toast**
            const boldMatch = line.match(/^\*\*(.+)\*\*$/);
            if (boldMatch) {
                recipe.title = boldMatch[1]
                    .replace(/[\u{1F300}-\u{1F9FF}]/gu, '')
                    .replace(/[\u{2600}-\u{26FF}]/gu, '')
                    .trim();
                continue;
            }
            // Skip section headers
            if (!/^(recipe|ingredients|instructions|directions|steps|method)[:\s]*$/i.test(line)) {
                // Check if this is the meta line (various formats)
                if (/[⏱🍽🔥]|time:|servings?:|difficulty:/i.test(line)) {
                    // Parse time - multiple formats
                    const timeMatch = line.match(/(?:⏱\s*(?:time:?)?\s*|time:\s*)(\d+)\s*(min|minutes?|hrs?|hours?)/i);
                    if (timeMatch) recipe.time = `${timeMatch[1]} ${timeMatch[2].toLowerCase().startsWith('h') ? 'hr' : 'min'}`;

                    // Parse servings - multiple formats
                    const servingsMatch = line.match(/(?:🍽\s*)?(?:servings?:?\s*)(\d+)/i);
                    if (servingsMatch) recipe.servings = `${servingsMatch[1]} servings`;

                    // Parse difficulty
                    const difficultyMatch = line.match(/(?:🔥\s*)?(?:difficulty:?\s*)(\w+)/i);
                    if (difficultyMatch) recipe.difficulty = difficultyMatch[1];
                    continue;
                }
            }
        }

        // Detect time from standalone line
        if (!recipe.time) {
            const timeMatch = line.match(/(\d+)\s*(min|minutes?|hrs?|hours?)/i);
            if (timeMatch) {
                recipe.time = `${timeMatch[1]} ${timeMatch[2].toLowerCase().startsWith('h') ? 'hr' : 'min'}`;
            }
        }

        // Detect servings from standalone line
        if (!recipe.servings) {
            const servingsMatch = line.match(/(\d+)\s*(servings?|portions?|people)/i);
            if (servingsMatch) {
                recipe.servings = `${servingsMatch[1]} servings`;
            }
        }

        // Detect sections - ONLY check section headers, not ingredient content
        const cleanLine = line.replace(/[*#_]+/g, '').trim().toLowerCase();

        // Section detection - be STRICT, only match actual headers
        // Must be a short line that IS the header, not a line that CONTAINS the word
        if (cleanLine.length < 25 && /^ingredients?[:\s]*$/i.test(cleanLine)) {
            section = 'ingredients';
            if (DEBUG_RECIPE) console.log(`[RecipeParser] Switched to INGREDIENTS section at line: "${line}"`);
            continue;
        }
        // Also detect "### Ingredients" markdown headers
        if (/^#{1,3}\s*ingredients?/i.test(line)) {
            section = 'ingredients';
            if (DEBUG_RECIPE) console.log(`[RecipeParser] Switched to INGREDIENTS section (markdown header): "${line}"`);
            continue;
        }
        if (cleanLine.length < 25 && /^(instructions?|directions?|steps?|method|preparation|how to make)[:\s]*$/i.test(cleanLine)) {
            section = 'instructions';
            if (DEBUG_RECIPE) console.log(`[RecipeParser] Switched to INSTRUCTIONS section at line: "${line}"`);
            continue;
        }
        // Also detect "### Instructions" markdown headers
        if (/^#{1,3}\s*(instructions?|directions?|steps?|method)/i.test(line)) {
            section = 'instructions';
            if (DEBUG_RECIPE) console.log(`[RecipeParser] Switched to INSTRUCTIONS section (markdown header): "${line}"`);
            continue;
        }

        // Parse ingredients
        if (section === 'ingredients') {
            // Remove bullet points and numbered list prefixes
            // Use \s+ (not \s*) after period to avoid matching decimals like "1.5 tsp"
            const cleaned = line.replace(/^[\-\*•□■◦▪▸►]\s*/, '').replace(/^\d+\.\s+/, '').trim();
            if (DEBUG_RECIPE) console.log(`[RecipeParser] In ingredients section, cleaned="${cleaned}", line="${line.substring(0,30)}"`);

            // Detect switch to instructions:
            // 1. Bold format like "**Preheat**" or "1. **Step**"
            // 2. Numbered step that looks like a cooking instruction (verb at start)
            // 3. Line starting with common cooking verbs
            const cookingVerbPattern = /^(heat|add|cook|stir|mix|pour|place|put|combine|whisk|bake|fry|boil|simmer|roast|grill|sauté|saute|preheat|season|serve|toss|slice|chop|dice|mince|drain|cover|let|remove|transfer|set)\b/i;

            if (/^\*\*[A-Z]/.test(line) || /^\d+\.\s*\*\*/.test(line)) {
                if (DEBUG_RECIPE) console.log(`[RecipeParser] SWITCH to instructions - bold format: "${line.substring(0,40)}"`);
                section = 'instructions';
                // Don't continue - process this line as instruction below
            } else if (/^#{2,4}\s*(step|phase|part)\s*\d/i.test(line)) {
                // Markdown step headers like "#### Step 1:" or "### Phase 2"
                if (DEBUG_RECIPE) console.log(`[RecipeParser] SWITCH to instructions - step header: "${line.substring(0,40)}"`);
                section = 'instructions';
                // Don't continue - process this line as instruction below
            } else if (/^\d+\.\s+/.test(line) && cookingVerbPattern.test(cleaned)) {
                // Numbered step starting with cooking verb = switch to instructions
                if (DEBUG_RECIPE) console.log(`[RecipeParser] SWITCH to instructions - numbered verb: "${line.substring(0,40)}"`);
                section = 'instructions';
                // Don't continue - process this line as instruction below
            } else if (cookingVerbPattern.test(line.trim()) && line.trim().length > 20) {
                // Long line starting with cooking verb (likely instruction without number)
                if (DEBUG_RECIPE) console.log(`[RecipeParser] SWITCH to instructions - cooking verb start: "${line.substring(0,40)}"`);
                section = 'instructions';
                // Don't continue - process this line as instruction below
            } else if (cleaned && cleaned.length > 2) {
                // Skip section headers
                if (/^(instructions?|directions?|steps?|preparation|method)/i.test(cleaned)) {
                    if (DEBUG_RECIPE) console.log(`[RecipeParser] SKIP - section header: "${cleaned}"`);
                    section = 'instructions';
                    continue;
                }
                // Skip meta lines, headers, time-only lines
                if (/^#{1,3}\s/.test(line)) {
                    if (DEBUG_RECIPE) console.log(`[RecipeParser] SKIP - markdown header: "${line.substring(0,30)}"`);
                    continue;
                }
                if (/^[\u{1F300}-\u{1F9FF}]/u.test(cleaned)) {
                    if (DEBUG_RECIPE) console.log(`[RecipeParser] SKIP - emoji start: "${cleaned.substring(0,20)}"`);
                    continue;
                }
                if (/^(⏱|🍽|🔥|time:|servings?:|difficulty:)/i.test(cleaned)) {
                    if (DEBUG_RECIPE) console.log(`[RecipeParser] SKIP - meta line: "${cleaned.substring(0,30)}"`);
                    continue;
                }
                // Skip time patterns: "7 min", "15 minutes", "1 hour", etc.
                if (/^\d+\s*(min|minutes?|hrs?|hours?|servings?)\b/i.test(cleaned)) {
                    if (DEBUG_RECIPE) console.log(`[RecipeParser] SKIP - time pattern: "${cleaned}"`);
                    continue;
                }
                // Skip explicit time labels: "prep time: 10", "cook: 30 min", "total time 45"
                // Must have "time" word OR be "prep/cook/total/bake/rest" followed by colon/number
                if (/^(prep|cook|total|bake|rest)\s*time\s*:?\s*\d+/i.test(cleaned) ||
                    /^(prep|cook|total|bake|rest)\s*:\s*\d+/i.test(cleaned) ||
                    /^time\s*:\s*\d+/i.test(cleaned)) {
                    if (DEBUG_RECIPE) console.log(`[RecipeParser] SKIP - time label: "${cleaned}"`);
                    continue;
                }
                // Skip closing questions
                if (/^(would you|do you|shall i|let me know|feel free)/i.test(cleaned)) {
                    if (DEBUG_RECIPE) console.log(`[RecipeParser] SKIP - closing question: "${cleaned.substring(0,30)}"`);
                    continue;
                }

                recipe.ingredients.push(cleaned);
                if (DEBUG_RECIPE) console.log(`[RecipeParser] Added ingredient: "${cleaned}"`);
                continue;
            } else {
                if (DEBUG_RECIPE) console.log(`[RecipeParser] In ingredients but no match, cleaned="${cleaned}", len=${cleaned ? cleaned.length : 0}`);
            }
        }

        // Parse instructions
        if (section === 'instructions') {
            // Clean the line - remove bullets, numbers, and clean up bold formatting
            let cleaned = line
                .replace(/^[\-\*•□■◦▪▸►]\s*/, '')
                .replace(/^\d+\.\s*/, '')
                .replace(/\*\*([^*]+)\*\*/g, '$1')  // Remove bold ** but keep text
                .trim();

            if (cleaned && cleaned.length > 2) {
                // Skip new recipe headers
                if (/^#{1,3}\s/.test(line)) continue;

                // Skip closing questions and meta content
                if (/^(would you|do you|shall i|let me know|feel free|variations?:|storage:|reheating:|serving suggestion)/i.test(cleaned)) {
                    continue;
                }

                // Capture tips (💡)
                if (/^(💡|tips?[:\s])/i.test(cleaned)) {
                    const tipText = cleaned.replace(/^(💡\s*|tips?[:\s*]+)/i, '').trim();
                    if (tipText) recipe.tip = recipe.tip ? recipe.tip + ' ' + tipText : tipText;
                    continue;
                }

                // Capture warnings (⚠️) - add to tip
                if (/^(⚠️|warning[:\s]|caution[:\s]|note[:\s])/i.test(cleaned)) {
                    const warnText = cleaned.replace(/^(⚠️\s*|warning[:\s*]+|caution[:\s*]+|note[:\s*]+)/i, '').trim();
                    if (warnText) recipe.tip = recipe.tip ? recipe.tip + ' ⚠️ ' + warnText : '⚠️ ' + warnText;
                    continue;
                }

                // Regular instruction - only add if it looks like a cooking step
                if (cleaned.length > 10) {
                    recipe.instructions.push(cleaned);
                }
            }
        }
    }

    // Default title if not found
    if (!recipe.title) recipe.title = 'Recipe';

    if (DEBUG_RECIPE) {
        console.log(`[RecipeParser] RESULT: title="${recipe.title}", ingredients=${recipe.ingredients.length}, instructions=${recipe.instructions.length}`);
        if (recipe.ingredients.length > 0) {
            console.log(`[RecipeParser] First 3 ingredients:`, recipe.ingredients.slice(0, 3));
        }
    }

    return recipe;
}

// ════════════════════════════════════════════════════════════════════════════
// RENDERING
// ════════════════════════════════════════════════════════════════════════════

/**
 * Escape HTML for safe rendering
 * @param {string} text - Raw text
 * @returns {string} Escaped HTML
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Parse ingredient string into structured parts
 * @param {string} rawText - Raw ingredient text
 * @returns {object} { qty, unit, item }
 */
function parseIngredient(rawText) {
    let fixed = rawText
        .replace(/^(\d+)(cup|tbsp|tsp|oz|lb|g|kg|ml|l|clove|slice|piece)s?/i, '$1 $2')
        .replace(/^(\d+)([a-z])/i, '$1 $2');

    const match = fixed.match(/^([\d\/]+(?:\s*-\s*[\d\/]+)?)\s*(cups?|tbsp|tsp|tablespoons?|teaspoons?|oz|lb|lbs?|g|kg|ml|l|cloves?|slices?|pieces?|bunch|handful|pinch|dash|large|medium|small)?\s*(.*)$/i);

    if (match && match[1]) {
        const qty = match[1].trim();
        const unit = match[2] ? match[2].trim() : '';
        const item = match[3] ? match[3].trim() : '';
        return { qty, unit, item };
    }
    return { qty: '', unit: '', item: fixed };
}

/**
 * Create a single recipe card element
 * @param {object} recipe - Parsed recipe object
 * @returns {HTMLElement} Recipe card element
 */
function createSingleRecipeCard(recipe) {
    const wrapper = document.createElement('div');
    wrapper.className = 'recipe-card';
    wrapper.style.cssText = `
        background: transparent !important;
        border: none !important;
        padding: 0 !important;
        max-width: 640px !important;
        margin: 0.5rem 0 1rem 0 !important;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif !important;
    `;

    // Build ingredients - clean vertical list
    const ingredientItems = recipe.ingredients.map(ing => {
        const p = parseIngredient(ing);
        if (p.qty && p.item) {
            const amt = escapeHtml(p.qty + (p.unit ? ' ' + p.unit : ''));
            const itm = escapeHtml(p.item);
            return `<div style="padding: 3px 0 !important; display: flex !important; align-items: baseline !important;">
                <span style="color: #555 !important; margin-right: 8px !important;">•</span>
                <span style="color: #e0e0e0 !important;">${amt}</span>
                <span style="color: #888 !important; margin-left: 6px !important;">${itm}</span>
            </div>`;
        }
        return `<div style="padding: 3px 0 !important; display: flex !important; align-items: baseline !important;">
            <span style="color: #555 !important; margin-right: 8px !important;">•</span>
            <span style="color: #888 !important;">${escapeHtml(p.item)}</span>
        </div>`;
    });

    const ingredientsList = `<div style="font-size: 13px !important;">${ingredientItems.join('')}</div>`;

    // Build instructions with elegant timeline
    const instructionsList = recipe.instructions.map((inst, i) => {
        const isLast = i === recipe.instructions.length - 1;
        return `<div style="display: flex !important; gap: 16px !important; position: relative !important;">
            <div style="display: flex !important; flex-direction: column !important; align-items: center !important; width: 24px !important; flex-shrink: 0 !important;">
                <div style="width: 8px !important; height: 8px !important; border-radius: 50% !important; background: #3b3b3b !important; border: 2px solid #555 !important; z-index: 1 !important;"></div>
                ${!isLast ? `<div style="width: 2px !important; flex: 1 !important; background: linear-gradient(180deg, #3b3b3b 0%, rgba(59,59,59,0.1) 100%) !important; margin: 4px 0 !important; min-height: 24px !important;"></div>` : ''}
            </div>
            <div style="flex: 1 !important; padding-bottom: ${isLast ? '0' : '20px'} !important; color: #b5b5b5 !important; font-size: 14px !important; line-height: 1.6 !important;">${escapeHtml(inst)}</div>
        </div>`;
    }).join('');

    // Meta line
    const metaItems = [];
    if (recipe.time) metaItems.push(recipe.time);
    if (recipe.servings) metaItems.push(recipe.servings);
    if (recipe.difficulty) metaItems.push(recipe.difficulty);
    const metaLine = metaItems.join('  ·  ');

    // Build HTML - only show sections with content
    let html = `
        <div style="margin-bottom: 16px !important;">
            <div style="font-size: 18px !important; font-weight: 600 !important; color: #f0f0f0 !important; margin: 0 0 6px 0 !important; letter-spacing: -0.3px !important; line-height: 1.3 !important;">${escapeHtml(recipe.title)}</div>
            ${metaLine ? `<div style="font-size: 12px !important; color: #666 !important; letter-spacing: 0.3px !important;">${metaLine}</div>` : ''}
        </div>
    `;

    if (recipe.ingredients.length > 0) {
        html += `
        <div style="margin-bottom: 24px !important;">
            ${ingredientsList}
        </div>
        `;
    }

    if (recipe.instructions.length > 0) {
        html += `
        <div>
            ${instructionsList}
        </div>
        `;
    }

    if (recipe.tip) {
        html += `
        <div style="margin-top: 16px !important; padding: 10px 12px !important; background: rgba(255,255,255,0.03) !important; border-radius: 6px !important; border-left: 2px solid #444 !important;">
            <div style="font-size: 13px !important; color: #888 !important; line-height: 1.5 !important;">${escapeHtml(recipe.tip)}</div>
        </div>
        `;
    }

    wrapper.innerHTML = html;

    return wrapper;
}

/**
 * Create recipe card(s) from content
 * @param {string} content - Raw recipe content
 * @returns {HTMLElement} Container with recipe card(s)
 */
function createRecipeCard(content) {
    const { intro, recipes } = splitRecipes(content);

    // Container for everything
    const container = document.createElement('div');
    container.className = 'recipe-cards-container';
    container.style.cssText = `
        display: flex !important;
        flex-direction: column !important;
        gap: 8px !important;
    `;

    // Add centered intro if exists
    if (intro && intro.length > 3) {
        const introEl = document.createElement('div');
        introEl.style.cssText = `
            text-align: center !important;
            font-size: 15px !important;
            font-weight: 500 !important;
            color: #888 !important;
            margin-bottom: 12px !important;
            letter-spacing: 0.2px !important;
        `;
        introEl.textContent = intro;
        container.appendChild(introEl);
    }

    // If single recipe, add single card
    if (recipes.length === 1) {
        const recipe = parseRecipe(recipes[0]);
        container.appendChild(createSingleRecipeCard(recipe));
        return container;
    }

    // Multiple recipes
    recipes.forEach((part, index) => {
        const recipe = parseRecipe(part);
        const card = createSingleRecipeCard(recipe);
        container.appendChild(card);
    });

    return container;
}

// ════════════════════════════════════════════════════════════════════════════
// EXPORTS
// ════════════════════════════════════════════════════════════════════════════

// Export for use in other modules
window.RecipeDetector = {
    // Detection
    isRecipe,
    calculateRecipeScore,

    // Parsing
    splitRecipes,
    parseRecipe,
    parseIngredient,

    // Rendering
    createRecipeCard,
    createSingleRecipeCard,

    // Config (for debugging/tuning)
    config: RecipeConfig,

    // Enable/disable dev logging
    setDevLogging: (enabled) => {
        RecipeConfig.DEV_LOGGING = !!enabled;
    }
};

// Also export individual functions for direct imports
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        isRecipe,
        calculateRecipeScore,
        splitRecipes,
        parseRecipe,
        parseIngredient,
        createRecipeCard,
        createSingleRecipeCard,
        RecipeConfig
    };
}
