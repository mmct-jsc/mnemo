/**
 * Sidebar TreeView provider. Three sections:
 *   - Active project (header + path)
 *   - Recent queries (last N from the audit log)
 *   - Pinned nodes (stored in extension state)
 */

import * as vscode from "vscode";
import { MnemoDaemon } from "./daemon";

const PINNED_KEY = "mnemo.pinned";

interface PinnedNode {
  id: string;
  name: string;
  type: string;
}

class TreeNode extends vscode.TreeItem {
  constructor(
    label: string,
    public readonly children: TreeNode[] | undefined,
    public readonly nodeId?: string
  ) {
    super(
      label,
      children ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None
    );
    if (nodeId) {
      this.command = {
        command: "mnemo.openNode",
        title: "Open node",
        arguments: [nodeId],
      };
      this.contextValue = "mnemo.node";
    }
  }
}

export class MnemoSidebarProvider implements vscode.TreeDataProvider<TreeNode> {
  private readonly _onDidChangeTreeData = new vscode.EventEmitter<TreeNode | undefined>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  constructor(
    private readonly daemon: MnemoDaemon,
    private readonly context: vscode.ExtensionContext
  ) {}

  refresh(): void {
    this._onDidChangeTreeData.fire(undefined);
  }

  getTreeItem(node: TreeNode): vscode.TreeItem {
    return node;
  }

  async getChildren(parent?: TreeNode): Promise<TreeNode[]> {
    if (parent) return parent.children ?? [];

    const out: TreeNode[] = [];

    // --- Active project ---
    const active = await this.daemon.getActiveProject();
    const activeLabel = active ? `Active: ${active.project_key}` : "Active: <none>";
    out.push(new TreeNode(activeLabel, []));

    // --- Pinned nodes ---
    const pinned = this.context.globalState.get<PinnedNode[]>(PINNED_KEY) ?? [];
    if (pinned.length) {
      const items = pinned.map(
        (p) =>
          new TreeNode(`${p.type.replace(/^memory_/, "")} · ${p.name}`, undefined, p.id)
      );
      out.push(new TreeNode(`Pinned (${pinned.length})`, items));
    } else {
      out.push(new TreeNode("Pinned (0)", []));
    }

    return out;
  }

  pinNode(node: PinnedNode): void {
    const existing = this.context.globalState.get<PinnedNode[]>(PINNED_KEY) ?? [];
    if (existing.find((n) => n.id === node.id)) return;
    void this.context.globalState.update(PINNED_KEY, [...existing, node]);
    this.refresh();
  }

  unpinNode(id: string): void {
    const existing = this.context.globalState.get<PinnedNode[]>(PINNED_KEY) ?? [];
    void this.context.globalState.update(
      PINNED_KEY,
      existing.filter((n) => n.id !== id)
    );
    this.refresh();
  }
}
