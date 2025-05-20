#!/bin/bash

# 定义目录路径
CSS_DIR="css"
JS_DIR="js"
WEBFONTS_DIR="webfonts"

# 创建目录（如果不存在）
mkdir -p $CSS_DIR
mkdir -p $JS_DIR
mkdir -p $WEBFONTS_DIR

# 下载 DaisyUI CSS
wget -O $CSS_DIR/daisyui.full.css https://unpkg.com/daisyui@3.7.4/dist/full.css

# 下载 Font Awesome CSS
wget -O $CSS_DIR/fontawesome.all.min.css https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css

# 下载 XLSX JS
wget -O $JS_DIR/xlsx.full.min.js https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js

# 下载 FileSaver JS
wget -O $JS_DIR/FileSaver.min.js https://cdn.jsdelivr.net/npm/file-saver@2.0.5/dist/FileSaver.min.js

# 提示用户手动生成 Tailwind CSS
echo "请手动生成 Tailwind CSS 并保存为 $CSS_DIR/tailwind.css"
echo "步骤如下："
echo "1. 创建一个 HTML 文件，包含以下内容： <script src=\"https://cdn.tailwindcss.com?plugins=typography,line-clamp,aspect-ratio\"></script>"
echo "2. 在浏览器中打开该 HTML 文件"
echo "3. 按 F12 打开开发者工具，找到生成的 <style> 标签"
echo "4. 复制 CSS 内容并保存到 $CSS_DIR/tailwind.css"

# 下载 Font Awesome 字体文件
FONT_FILES=(
    "fa-brands-400.woff2"
    "fa-brands-400.ttf"
    "fa-regular-400.woff2"
    "fa-regular-400.ttf"
    "fa-solid-900.woff2"
    "fa-solid-900.ttf"
    "fa-v4compatibility.woff2"
    "fa-v4compatibility.ttf"
)

for file in "${FONT_FILES[@]}"; do
    wget -P $WEBFONTS_DIR https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/webfonts/$file
done

# 更新 Font Awesome CSS 中的字体路径（如果需要）
sed -i 's|url(.*?/webfonts/|url(../webfonts/|g' $CSS_DIR/fontawesome.all.min.css

echo "脚本执行完成。请按照上述 Tailwind CSS 说明操作。"