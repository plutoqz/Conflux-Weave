import katex from "katex";
import { marked } from "marked";

/**
 * Remove legacy repetitive template titles like 【本节研读与核心论点】
 */
export function cleanDocumentNoteText(text: string): string {
  if (!text) return "";
  return text.replace(/^\s*(\*{0,2}【本节研读与核心论点】\*{0,2}\s*)+/gm, "");
}

/**
 * Robust markdown renderer with KaTeX math formula support.
 * Safely handles display ($$, \[) and inline ($, \() LaTeX math equations.
 */
export function renderMarkdownWithMath(mdText: string): string {
  if (!mdText) return "";
  const cleaned = cleanDocumentNoteText(mdText);

  // Preserve math expressions by replacing them with unique placeholders
  // before passing through marked.parse so underscores/asterisks aren't corrupted.
  const mathTokens: { id: string; html: string }[] = [];
  let tokenCounter = 0;

  // 1. Display math: $$ ... $$
  let processed = cleaned.replace(/\$\$([\s\S]*?)\$\$/g, (_, eq) => {
    const id = `___MATH_BLOCK_${tokenCounter++}___`;
    try {
      const rendered = katex.renderToString(eq.trim(), {
        displayMode: true,
        throwOnError: false,
      });
      mathTokens.push({ id, html: `<div class="katex-display my-3 overflow-x-auto text-center">${rendered}</div>` });
    } catch {
      mathTokens.push({ id, html: `<pre class="text-xs bg-muted p-2 rounded">$$${eq}$$</pre>` });
    }
    return id;
  });

  // 2. Display math: \[ ... \]
  processed = processed.replace(/\\\[([\s\S]*?)\\\]/g, (_, eq) => {
    const id = `___MATH_BLOCK_${tokenCounter++}___`;
    try {
      const rendered = katex.renderToString(eq.trim(), {
        displayMode: true,
        throwOnError: false,
      });
      mathTokens.push({ id, html: `<div class="katex-display my-3 overflow-x-auto text-center">${rendered}</div>` });
    } catch {
      mathTokens.push({ id, html: `<pre class="text-xs bg-muted p-2 rounded">\\[${eq}\\]</pre>` });
    }
    return id;
  });

  // 3. Inline math: \( ... \)
  processed = processed.replace(/\\\(([\s\S]*?)\\\)/g, (_, eq) => {
    const id = `___MATH_INLINE_${tokenCounter++}___`;
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

  // 4. Inline math: $ ... $ (exclude pure numbers like $10 or empty $$)
  processed = processed.replace(/(?<!\\)\$([^\$\n]+?)(?<!\\)\$/g, (fullMatch, eq) => {
    if (/^\s*\d+([.,]\d+)?\s*$/.test(eq)) {
      return fullMatch;
    }
    const id = `___MATH_INLINE_${tokenCounter++}___`;
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

  // Parse markdown
  let html = marked.parse(processed, { gfm: true, breaks: true }) as string;

  // Restore math tokens
  for (const token of mathTokens) {
    html = html.replace(token.id, token.html);
  }

  return html;
}
