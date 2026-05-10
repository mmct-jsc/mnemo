/**
 * @mnemo chat participant. Registered with vscode.chat.createChatParticipant.
 *
 * When the user types `@mnemo what's our MQTT auth pattern?` in any
 * chat-participant-aware extension (Copilot Chat, Cody), this handler:
 *
 *   1. POSTs the prompt to /v1/query.
 *   2. Streams formatted hits as chat references with [mnemo:<id>] citations.
 *   3. Supports slash sub-commands /recall, /add, /sources.
 */

import * as vscode from "vscode";
import { MnemoDaemon } from "./daemon";

export function registerChatParticipant(
  context: vscode.ExtensionContext,
  daemon: MnemoDaemon
): vscode.Disposable {
  const handler: vscode.ChatRequestHandler = async (request, _ctx, stream, token) => {
    if (token.isCancellationRequested) return {};

    const slash = (request.command ?? "").toLowerCase();

    if (slash === "sources") {
      const sources = await daemon.listSources();
      if (!sources.length) {
        stream.markdown("_No sources registered. Run `mnemo source add <path>`._");
        return {};
      }
      stream.markdown(`### mnemo sources (${sources.length})\n\n`);
      for (const s of sources) {
        const enabled = s.enabled ? "" : " _(disabled)_";
        stream.markdown(`- **${s.kind}**${enabled} \`${s.path}\``);
        if (s.project_key) stream.markdown(` — _${s.project_key}_`);
        stream.markdown("\n");
      }
      return {};
    }

    if (slash === "add") {
      // 'add' is a TODO -- direct add via HTTP isn't wired in v1.1
      // (see daemon.addNote stub). Surface the right user action.
      stream.markdown(
        "Adding notes from chat lands in **v1.2**. For now, run\n\n" +
          "```\n/mnemo-add\n```\n\n" +
          "in a Claude Code session, or open the UI:\n\n" +
          `[mnemo dashboard](${MnemoDaemon.configuredUrl()})`
      );
      return {};
    }

    // Default + /recall path: query mnemo and stream hits.
    const prompt = request.prompt.trim();
    if (!prompt) {
      stream.markdown(
        "Ask me about your project memory. Examples:\n\n" +
          "- `@mnemo what's our MQTT auth pattern?`\n" +
          "- `@mnemo /recall recent debug sessions`\n" +
          "- `@mnemo /sources` — list registered sources\n"
      );
      return {};
    }

    stream.progress("Querying mnemo...");
    const result = await daemon.query(prompt);
    if (!result) {
      stream.markdown(
        `_mnemo daemon unreachable at ${MnemoDaemon.configuredUrl()}._\n\n` +
          "Start it with `mnemo daemon start` and try again."
      );
      return {};
    }

    if (!result.hits.length) {
      stream.markdown("_No relevant memory found._");
      return {};
    }

    const intent = result.intent_tags.filter((t) => t && t !== "none").join(", ");
    const header = `**${result.hits.length} hit${result.hits.length === 1 ? "" : "s"}**` +
      (intent ? ` · _intent: ${intent}_` : "") +
      ` · _${result.tokens_used} tokens_\n\n`;
    stream.markdown(header);

    for (const h of result.hits) {
      const cite = h.citation || `[mnemo:${h.id}]`;
      const desc = (h.description ?? "").replace(/\n/g, " ").trim();
      stream.markdown(`### ${h.name} \`${cite}\`\n\n`);
      if (h.type) stream.markdown(`_${h.type.replace(/^memory_/, "")}_ · `);
      stream.markdown(`score \`${h.score.toFixed(3)}\`\n\n`);
      if (desc) stream.markdown(`${desc}\n\n`);
      if (h.body) {
        const snippet = h.body.length > 400 ? h.body.slice(0, 400).trimEnd() + "..." : h.body;
        stream.markdown("```\n" + snippet + "\n```\n\n");
      }
      // Reference so the user can open the node in the side panel.
      stream.reference(
        vscode.Uri.parse(`${MnemoDaemon.configuredUrl()}/node/${h.id}`)
      );
    }

    return {};
  };

  const participant = vscode.chat.createChatParticipant("mmct-jsc.mnemo", handler);
  participant.iconPath = new vscode.ThemeIcon("symbol-misc");
  context.subscriptions.push(participant);
  return participant;
}
