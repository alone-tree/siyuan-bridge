"use strict";

const SKIP_BLOCK_TYPES = new Set(["d"]);
const SUBTREE_MARKDOWN_BLOCK_TYPES = new Set(["i", "l", "t"]);
const COMMENT_ONLY_BLOCK_TYPES = new Set(["s"]);
const CHILD_TRAVERSAL_BLOCK_TYPES = new Set(["h", "l", "s"]);
const DATABASE_BLOCK_TYPES = new Set(["av"]);
const STRUCTURE_ACTIONS = new Set(["insert", "delete", "move", "append"]);

function blockField(block, ...names) {
  if (!block || typeof block !== "object") {
    return "";
  }
  for (const name of names) {
    const value = block[name];
    if (value !== undefined && value !== null) {
      return String(value);
    }
  }
  return "";
}

function semanticBlockType(rawType, subtype, markdown) {
  if (rawType === "p" && /!?\[[^\]]*\]\(assets\/[^)]+\)/.test(markdown || "")) {
    return "attachment";
  }
  return {
    h: "heading",
    p: "paragraph",
    l: "list",
    i: "list_item",
    t: "table",
    c: "code",
    s: "superblock",
    av: "database",
    b: "blockquote",
    m: "math",
    html: "html",
    iframe: "iframe",
    video: "video",
    audio: "audio",
    widget: "widget",
    tb: "thematic_break",
  }[rawType] || rawType || "unknown";
}

function childBlocks(childrenByParent, blockId) {
  const children = childrenByParent && childrenByParent[blockId];
  return Array.isArray(children) ? children : [];
}

function displayBlockNeedsChildren(block) {
  const blockType = blockField(block, "type");
  const markdown = blockField(block, "markdown").trim();
  if (SKIP_BLOCK_TYPES.has(blockType)) {
    return CHILD_TRAVERSAL_BLOCK_TYPES.has(blockType);
  }
  if (DATABASE_BLOCK_TYPES.has(blockType)) {
    return false;
  }
  if (blockType === "l" && !markdown) {
    return true;
  }
  if (!markdown && !COMMENT_ONLY_BLOCK_TYPES.has(blockType)) {
    return CHILD_TRAVERSAL_BLOCK_TYPES.has(blockType);
  }
  if (SUBTREE_MARKDOWN_BLOCK_TYPES.has(blockType)) {
    return false;
  }
  return CHILD_TRAVERSAL_BLOCK_TYPES.has(blockType);
}

function collectDisplayBlockIndexes(rootId, childrenByParent) {
  const blocks = [];
  const visited = new Set();

  function visit(block) {
    const blockId = blockField(block, "id");
    if (!blockId || visited.has(blockId)) {
      return;
    }
    visited.add(blockId);

    const blockType = blockField(block, "type");
    if (SKIP_BLOCK_TYPES.has(blockType)) {
      if (CHILD_TRAVERSAL_BLOCK_TYPES.has(blockType)) {
        for (const child of childBlocks(childrenByParent, blockId)) {
          visit(child);
        }
      }
      return;
    }

    const subtype = blockField(block, "subtype", "subType");
    const markdown = blockField(block, "markdown");

    if (DATABASE_BLOCK_TYPES.has(blockType)) {
      blocks.push({
        index: blocks.length + 1,
        id: blockId,
        type: semanticBlockType(blockType, subtype, markdown),
      });
      return;
    }

    if (blockType === "l" && !markdown.trim()) {
      for (const child of childBlocks(childrenByParent, blockId)) {
        visit(child);
      }
      return;
    }

    if (!markdown.trim() && !COMMENT_ONLY_BLOCK_TYPES.has(blockType)) {
      if (CHILD_TRAVERSAL_BLOCK_TYPES.has(blockType)) {
        for (const child of childBlocks(childrenByParent, blockId)) {
          visit(child);
        }
      }
      return;
    }

    blocks.push({
      index: blocks.length + 1,
      id: blockId,
      type: semanticBlockType(blockType, subtype, markdown),
    });

    if (SUBTREE_MARKDOWN_BLOCK_TYPES.has(blockType)) {
      return;
    }
    if (CHILD_TRAVERSAL_BLOCK_TYPES.has(blockType)) {
      for (const child of childBlocks(childrenByParent, blockId)) {
        visit(child);
      }
    }
  }

  for (const child of childBlocks(childrenByParent, rootId)) {
    visit(child);
  }
  return blocks;
}

function transactionChangesBlockStructure(detail) {
  if (!detail || detail.cmd !== "transactions") {
    return false;
  }
  const entries = Array.isArray(detail.data) ? detail.data : [];
  for (const entry of entries) {
    const operations = entry && Array.isArray(entry.doOperations) ? entry.doOperations : [];
    for (const operation of operations) {
      if (operation && STRUCTURE_ACTIONS.has(String(operation.action || ""))) {
        return true;
      }
    }
  }
  return false;
}

module.exports = {
  collectDisplayBlockIndexes,
  displayBlockNeedsChildren,
  semanticBlockType,
  transactionChangesBlockStructure,
  blockField,
};
