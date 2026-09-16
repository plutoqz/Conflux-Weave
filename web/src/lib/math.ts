import katex from "katex";
import { marked } from "marked";
import DOMPurify from "dompurify";

/**
 * Sanitize HTML with strict allowlists for typography, KaTeX math symbols, and citation badges.
 */
export function sanitizeHtml(rawHtml: string): string {
  if (typeof window !== "undefined") {
    const purifier = typeof (DOMPurify as any).sanitize === "function" ? DOMPurify : (DOMPurify as any)(window);
    return purifier.sanitize(rawHtml, {
      USE_PROFILES: { html: true, mathMl: true, svg: true },
      ADD_TAGS: [
        "math",
        "annotation",
        "semantics",
        "mrow",
        "mo",
        "mi",
        "mn",
        "mspace",
        "mover",
        "munder",
        "msubsup",
        "mfrac",
        "mroot",
        "msqrt",
        "mstyle",
        "mtext",
      ],
      ADD_ATTR: ["target", "data-cite", "aria-hidden", "class", "id", "href"],
    });
  }
  return rawHtml
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
    .replace(/on\w+\s*=\s*(?:["'][^"']*["']|[^\s>]+)/gi, "")
    .replace(/javascript\s*:/gi, "");
}

/**
 * Remove legacy repetitive template titles like 【本节研读与核心论点】
 */
export function cleanDocumentNoteText(text: string): string {
  if (!text) return "";
  return text.replace(/^\s*(\*{0,2}【本节研读与核心论点】\*{0,2}\s*)+/gm, "");
}

export interface RenderMarkdownOptions {
  enableCitations?: boolean;
}

/**
 * Robust markdown renderer with KaTeX math formula support and optional academic citation pills.
 * Safely handles display ($$, \[) and inline ($, \() LaTeX math equations.
 */
export function renderMarkdownWithMath(
  mdText: string,
  options?: RenderMarkdownOptions
): string {
  if (!mdText) return "";
  const cleaned = cleanDocumentNoteText(mdText);

  // Preserve math expressions by replacing them with unique alphanumeric placeholders
  // that CommonMark / GFM parsers cannot confuse with emphasis (avoiding underscores/asterisks).
  const mathTokens: { id: string; html: string }[] = [];
  let tokenCounter = 0;

  // 1. Display math: $$ ... $$
  let processed = cleaned.replace(/\$\$([\s\S]*?)\$\$/g, (_, eq) => {
    const id = `@@KATEXBLOCK${tokenCounter++}@@`;
    try {
      const rendered = katex.renderToString(eq.trim(), {
        displayMode: true,
        throwOnError: false,
      });
      mathTokens.push({
        id,
        html: `<div class="katex-display my-3 overflow-x-auto text-center">${rendered}</div>`,
      });
    } catch {
      mathTokens.push({
        id,
        html: `<pre class="text-xs bg-muted p-2 rounded">$$${eq}$$</pre>`,
      });
    }
    return id;
  });

  // 2. Display math: \[ ... \]
  processed = processed.replace(/\\\[([\s\S]*?)\\\]/g, (_, eq) => {
    const id = `@@KATEXBLOCK${tokenCounter++}@@`;
    try {
      const rendered = katex.renderToString(eq.trim(), {
        displayMode: true,
        throwOnError: false,
      });
      mathTokens.push({
        id,
        html: `<div class="katex-display my-3 overflow-x-auto text-center">${rendered}</div>`,
      });
    } catch {
      mathTokens.push({
        id,
        html: `<pre class="text-xs bg-muted p-2 rounded">\\[${eq}\\]</pre>`,
      });
    }
    return id;
  });

  // 3. Inline math: \( ... \)
  processed = processed.replace(/\\\(([\s\S]*?)\\\)/g, (_, eq) => {
    const id = `@@KATEXINLINE${tokenCounter++}@@`;
    try {
      const rendered = katex.renderToString(eq.trim(), {
        displayMode: false,
        throwOnError: false,
      });
      mathTokens.push({ id, html: rendered });
    } catch {
      mathTokens.push({ id, html: `\\(${eq}\\)` });
    }
    return id;
  });

  // 4. Inline math: $ ... $ (exclude pure numbers like $10, $3.50 or empty $$)
  processed = processed.replace(/(?<!\\)\$([^\$\n]+?)(?<!\\)\$/g, (fullMatch, eq) => {
    if (/^\s*\d+([.,]\d+)?\s*$/.test(eq)) {
      return fullMatch;
    }
    const id = `@@KATEXINLINE${tokenCounter++}@@`;
    try {
      const rendered = katex.renderToString(eq.trim(), {
        displayMode: false,
        throwOnError: false,
      });
      mathTokens.push({ id, html: rendered });
    } catch {
      mathTokens.push({ id, html: `$${eq}$` });
    }
    return id;
  });

  // 5. Optional academic citation badge transformations
  if (options?.enableCitations) {
    // 5.1 Mark reference list anchors (e.g. - [1] or - [sq1-claim-0001] or 1. [sq1-claim-0001])
    processed = processed.replace(
      /^(\s*(?:-\s*|\d+\.\s*)\[)((?:sq\d+-|live-|paper-|review-|managed-)?claim-\d+|\d+|[a-zA-Z0-9_\-\.]+)(\]\s*)/gim,
      "$1@@REF_TARGET_$2@@$3"
    );

    // 5.2 Also mark inline claim tags in summary: e.g. 声明标识：`sq1-claim-0001`
    processed = processed.replace(
      /(声明标识：`?)((?:sq\d+-|live-|paper-|review-|managed-)?claim-\d+)(`?)/g,
      `$1<span id="cite-$2" class="citation-source-target font-mono font-bold text-primary underline">$2</span>$3`
    );

    // 5.3 Transform inline bracket citations (e.g. [1] or [sq1-claim-0001]) into interactive badges
    processed = processed.replace(
      /\[((?:sq\d+-|live-|paper-|review-|managed-)?claim-\d+|\d+)\]/g,
      (match, id) => {
        let displayLabel = id;
        const sqMatch = id.match(/^sq(\d+)-claim-0*(\d+)$/i);
        if (sqMatch) {
          displayLabel = `Claim ${sqMatch[1]}.${sqMatch[2]}`;
        } else {
          const claimMatch = id.match(/^claim-0*(\d+)$/i);
          if (claimMatch) {
            displayLabel = `Claim ${claimMatch[1]}`;
          }
        }
        return `<a class="citation-badge" href="#cite-${id}" data-cite="${id}">[${displayLabel}]</a>`;
      }
    );

    // 5.4 Restore reference targets with anchor ID
    processed = processed.replace(
      /@@REF_TARGET_((?:sq\d+-|live-|paper-|review-|managed-)?claim-\d+|\d+|[a-zA-Z0-9_\-\.]+)@@/g,
      `<span id="cite-$1" class="citation-source-target font-mono font-bold text-primary inline-block mr-1.5">[<span class="underline">$1</span>]</span>`
    );
  }

  // Parse markdown
  let html = marked.parse(processed, { gfm: true, breaks: true }) as string;

  // Restore math tokens safely
  for (const token of mathTokens) {
    html = html.split(token.id).join(token.html);
  }

  return sanitizeHtml(html);
}
