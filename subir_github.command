#!/bin/bash
# Doble clic para ejecutar — sube el código a GitHub
set -e
cd "$(dirname "$0")"

echo ""
echo "🚀 Subiendo spufa-bot a GitHub..."
echo ""

# Limpiar cualquier repo git roto anterior
rm -rf .git

# Inicializar repo nuevo
git init
git branch -M main
git config user.email "bot@spufa.app"
git config user.name "Spufa Bot"

# Añadir todos los archivos (excepto los ignorados por .gitignore)
git add -A

# Commit inicial
git commit -m "Initial commit — Spufa bot with referral system"

# Conectar con GitHub y subir
git remote add origin https://github.com/administracion168/spufa-bot.git
git push -u origin main --force

echo ""
echo "✅ ¡Código subido a GitHub correctamente!"
echo "   https://github.com/administracion168/spufa-bot"
echo ""
echo "Puedes cerrar esta ventana."
read -p "Presiona Enter para cerrar..."
