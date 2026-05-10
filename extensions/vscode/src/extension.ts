/**
 * mnemo VS Code extension entry point. Wires up:
 *   - Status bar item (daemon health + active project)
 *   - Palette commands (mnemo.query, addNote, setActiveProject, openUI, reindex)
 *   - Sidebar TreeView (active project + pinned nodes)
 *   - @mnemo chat participant (Copilot Chat / Cody / etc.)
 */

import * as vscode from "vscode";
import { MnemoDaemon } from "./daemon";
import { MnemoSidebarProvider } from "./sidebar";
import { registerChatParticipant } from "./chat";

let statusBar: vscode.StatusBarItem | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const daemon = new MnemoDaemon();

  // --- Status bar ---
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = "mnemo.openUI";
  context.subscriptions.push(statusBar);
  await refreshStatusBar(daemon);
  // Refresh every 30s so daemon-up/down state isn't stale.
  const tick = setInterval(() => void refreshStatusBar(daemon), 30_000);
  context.subscriptions.push({ dispose: () => clearInterval(tick) });

  // --- Sidebar ---
  const sidebar = new MnemoSidebarProvider(daemon, context);
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("mnemo.sidebar", sidebar)
  );

  // --- Auto-activate project on startup if configured ---
  const cfg = vscode.workspace.getConfiguration("mnemo");
  if (cfg.get<boolean>("autoActivate") ?? true) {
    const folders = vscode.workspace.workspaceFolders;
    if (folders && folders.length) {
      const path = folders[0].uri.fsPath;
      void daemon.setActiveProject(path).then(() => {
        sidebar.refresh();
        void refreshStatusBar(daemon);
      });
    }
  }

  // --- Commands ---
  context.subscriptions.push(
    vscode.commands.registerCommand("mnemo.query", () => cmdQuery(daemon)),
    vscode.commands.registerCommand("mnemo.addNote", () => cmdAddNote(daemon)),
    vscode.commands.registerCommand("mnemo.setActiveProject", () =>
      cmdSetActiveProject(daemon, sidebar)
    ),
    vscode.commands.registerCommand("mnemo.openUI", () => cmdOpenUI()),
    vscode.commands.registerCommand("mnemo.reindex", () => cmdReindex(daemon, sidebar)),
    vscode.commands.registerCommand("mnemo.openNode", (id: string) => cmdOpenNode(id))
  );

  // --- Chat participant ---
  registerChatParticipant(context, daemon);
}

export function deactivate(): void {
  statusBar?.dispose();
}

async function refreshStatusBar(daemon: MnemoDaemon): Promise<void> {
  if (!statusBar) return;
  const [health, active] = await Promise.all([daemon.health(), daemon.getActiveProject()]);
  if (!health) {
    statusBar.text = "$(circle-slash) mnemo · down";
    statusBar.tooltip = `mnemo daemon unreachable at ${MnemoDaemon.configuredUrl()}`;
    statusBar.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
  } else {
    const proj = active?.project_key ?? "no project";
    statusBar.text = `$(brain) mnemo · ${proj}`;
    statusBar.tooltip = `mnemo v${health.version} · click to open UI`;
    statusBar.backgroundColor = undefined;
  }
  statusBar.show();
}

// --- Command handlers -----------------------------------------------------

async function cmdQuery(daemon: MnemoDaemon): Promise<void> {
  // Default the query to the editor's selection if any, else prompt.
  const editor = vscode.window.activeTextEditor;
  let initial = "";
  if (editor && !editor.selection.isEmpty) {
    initial = editor.document.getText(editor.selection).slice(0, 500);
  }
  const prompt = await vscode.window.showInputBox({
    prompt: "mnemo query",
    placeHolder: "what's our MQTT auth pattern?",
    value: initial,
  });
  if (!prompt) return;

  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "Querying mnemo..." },
    async () => {
      const result = await daemon.query(prompt);
      if (!result) {
        void vscode.window.showWarningMessage(
          `mnemo daemon unreachable at ${MnemoDaemon.configuredUrl()}.`
        );
        return;
      }
      if (!result.hits.length) {
        void vscode.window.showInformationMessage("No relevant memory found.");
        return;
      }
      // Show results in a virtual document so the user can read them in
      // the editor instead of a tiny notification popup.
      const doc = await vscode.workspace.openTextDocument({
        language: "markdown",
        content: formatQueryAsMarkdown(prompt, result),
      });
      void vscode.window.showTextDocument(doc, { preview: true });
    }
  );
}

function formatQueryAsMarkdown(prompt: string, result: { hits: any[]; intent_tags: string[]; tokens_used: number }): string {
  const lines: string[] = [];
  lines.push(`# mnemo query`);
  lines.push("");
  lines.push(`> ${prompt}`);
  lines.push("");
  const intent = result.intent_tags.filter((t) => t && t !== "none").join(", ");
  lines.push(
    `_${result.hits.length} hit${result.hits.length === 1 ? "" : "s"}_` +
      (intent ? ` · intent: ${intent}` : "") +
      ` · ${result.tokens_used} tokens`
  );
  lines.push("");
  for (const h of result.hits) {
    lines.push(`## ${h.name} \`${h.citation ?? "[mnemo:" + h.id + "]"}\``);
    lines.push("");
    lines.push(`*${h.type ?? ""}* · score \`${(h.score ?? 0).toFixed(3)}\``);
    lines.push("");
    if (h.description) lines.push(h.description);
    lines.push("");
    if (h.body) {
      const snippet = h.body.length > 600 ? h.body.slice(0, 600) + "..." : h.body;
      lines.push("```");
      lines.push(snippet);
      lines.push("```");
      lines.push("");
    }
  }
  return lines.join("\n");
}

async function cmdAddNote(_daemon: MnemoDaemon): Promise<void> {
  // v1.1: notes go through the daemon's filesystem watcher. Open the UI
  // with a hint at the dashboard. /v1/nodes gets a POST endpoint in v1.2.
  void vscode.env.openExternal(vscode.Uri.parse(MnemoDaemon.configuredUrl() + "/nodes-page"));
}

async function cmdSetActiveProject(
  daemon: MnemoDaemon,
  sidebar: MnemoSidebarProvider
): Promise<void> {
  const folders = vscode.workspace.workspaceFolders;
  let initial = folders && folders.length ? folders[0].uri.fsPath : "";
  const path = await vscode.window.showInputBox({
    prompt: "Set active project (absolute path)",
    value: initial,
  });
  if (!path) return;
  const result = await daemon.setActiveProject(path);
  if (!result) {
    void vscode.window.showErrorMessage("Failed to set active project.");
    return;
  }
  void vscode.window.showInformationMessage(`Active project: ${result.project_key}`);
  sidebar.refresh();
  void refreshStatusBar(daemon);
}

function cmdOpenUI(): void {
  void vscode.env.openExternal(vscode.Uri.parse(MnemoDaemon.configuredUrl()));
}

async function cmdReindex(daemon: MnemoDaemon, sidebar: MnemoSidebarProvider): Promise<void> {
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "Reindexing mnemo..." },
    async () => {
      const r = await daemon.reindex();
      if (!r) {
        void vscode.window.showErrorMessage("Reindex failed.");
        return;
      }
      const total = r.added + r.updated + r.unchanged;
      const msg =
        r.added || r.updated || r.removed
          ? `Reindex done: +${r.added} new, ~${r.updated} updated, -${r.removed} removed`
          : `All ${total} nodes already up to date`;
      void vscode.window.showInformationMessage(msg);
      sidebar.refresh();
    }
  );
}

function cmdOpenNode(id: string): void {
  void vscode.env.openExternal(vscode.Uri.parse(MnemoDaemon.configuredUrl() + "/node/" + id));
}
