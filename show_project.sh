#!/bin/bash

TARGET_DIR=${1:-crud_app}

if [ ! -d "$TARGET_DIR" ]; then
    echo "Directory $TARGET_DIR does not exist."
    exit 1
fi

# 除外リスト（ファイル・ディレクトリ・パターン）
EXCLUDES=(
    "show_project.sh"
    ".git"
    "venv"
    ".venv"
    "env"
    "__pycache__"
    "node_modules"
    "migrations"
    "static"
    "uploads"
    ".idea"
    ".vscode"
    "*.pyc"
    "*.db"
    "*.sqlite3"
    "*.log"
    "*.egg-info"
    "*.tar.gz"
    "*.zip"
    "*.swp"
    ".DS_Store"
    ".env"
    ".gitignore"
    "dist"
    "build"
    "tmp"
)

# tree用の除外パターン（'|'区切りで結合）
TREE_EXCLUDES=$(IFS="|"; echo "${EXCLUDES[*]}")

# find用の-pruneパターン自動生成
FIND_PRUNE_EXPR=""
for pattern in "${EXCLUDES[@]}"; do
    if [[ "$pattern" == *"."* || "$pattern" == "*"* ]]; then
        # 拡張子やワイルドカードはファイル名判定
        FIND_PRUNE_EXPR="$FIND_PRUNE_EXPR -name \"$pattern\" -o"
    else
        # それ以外はディレクトリ名で判定
        FIND_PRUNE_EXPR="$FIND_PRUNE_EXPR -name \"$pattern\" -o"
    fi
done
# 末尾の -o を削除
FIND_PRUNE_EXPR=${FIND_PRUNE_EXPR% -o}

echo "========== ファイル構造 ($TARGET_DIR) =========="
if command -v tree > /dev/null; then
    tree "$TARGET_DIR" -I "$TREE_EXCLUDES"
else
    eval "find \"$TARGET_DIR\" \\( $FIND_PRUNE_EXPR \\) -prune -o -type f -print | sed \"s|$TARGET_DIR/||\""
fi

echo
echo "========== ファイル内容 =========="

eval "find \"$TARGET_DIR\" \\( $FIND_PRUNE_EXPR \\) -prune -o -type f -print" | while read file; do
    if file "$file" | grep -qE 'text|ASCII|Unicode'; then
        echo
        echo "----- $file -----"
        head -c 1048576 "$file"
        echo
    else
        echo
        echo "----- $file (バイナリファイル/非表示) -----"
    fi
done
