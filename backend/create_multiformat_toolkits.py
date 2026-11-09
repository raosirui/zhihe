"""创建多格式工具包，测试 Word/PDF/PPT/HTML/Markdown 等各种格式"""
import os
import sys
sys.path.insert(0, '/root/workspace/zhihe/backend')
os.chdir('/root/workspace/zhihe/backend')

from dotenv import load_dotenv
load_dotenv('/etc/zhihe/backend.env')

from app import create_app
from models import db, User, Toolkit, Lesson
from werkzeug.datastructures import FileStorage
from document_converter import detect_content_type, convert_document, save_raw_file

def create_test_files():
    """创建各种格式的测试文件"""
    test_dir = '/root/workspace/test_files'
    os.makedirs(test_dir, exist_ok=True)

    # 1. 创建 Word 文档
    from docx import Document
    doc = Document()
    doc.add_heading('单细胞 RNA-seq 实验指南', 0)
    doc.add_paragraph('本实验指南详细介绍单细胞转录组分析的完整流程。')

    doc.add_heading('1. 样本制备', level=1)
    doc.add_paragraph('• 细胞悬液制备：使用胶原酶 IV 消化组织 30-60 分钟', style='List Bullet')
    doc.add_paragraph('• 细胞活力检测：台盼蓝染色，活力需 > 80%', style='List Bullet')
    doc.add_paragraph('• 细胞浓度调整：700-1200 cells/μL', style='List Bullet')

    doc.add_heading('2. 上机测序', level=1)
    doc.add_paragraph('使用 10x Genomics Chromium 平台进行单细胞捕获和测序。')

    doc.add_heading('3. 数据分析', level=1)
    doc.add_paragraph('```r')
    doc.add_paragraph('library(Seurat)')
    doc.add_paragraph('pbmc <- Read10X(data.dir = "path/to/data/")')

    word_path = os.path.join(test_dir, 'single_cell_guide.docx')
    doc.save(word_path)
    print(f"✓ 创建 Word 文档: {word_path}")

    # 2. 创建 PDF 文档
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import cm

    pdf_path = os.path.join(test_dir, 'crispr_protocol.pdf')
    c = canvas.Canvas(pdf_path, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 20)
    c.drawString(2*cm, height - 3*cm, "CRISPR-Cas9 Protocol")

    c.setFont("Helvetica", 12)
    y = height - 5*cm
    c.drawString(2*cm, y, "1. sgRNA Design")
    y -= 0.8*cm
    c.drawString(2.5*cm, y, "- Target sequence: 20bp + PAM (NGG)")
    y -= 0.8*cm
    c.drawString(2.5*cm, y, "- Off-target analysis: CFD score")

    y -= 1.2*cm
    c.drawString(2*cm, y, "2. Vector Construction")
    y -= 0.8*cm
    c.drawString(2.5*cm, y, "- Use px459 or lentiCRISPR v2")
    y -= 0.8*cm
    c.drawString(2.5*cm, y, "- Clone sgRNA into BsmBI site")

    y -= 1.2*cm
    c.drawString(2*cm, y, "3. Virus Packaging")
    y -= 0.8*cm
    c.drawString(2.5*cm, y, "- Transfect 293T cells")
    y -= 0.8*cm
    c.drawString(2.5*cm, y, "- Collect virus at 48h and 72h")

    c.save()
    print(f"✓ 创建 PDF 文档: {pdf_path}")

    # 3. 创建 PPT 演示文稿
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()

    # 标题页
    slide_layout = prs.slide_layouts[0]
    slide = prs.slides.add_slide(slide_layout)
    title = slide.shapes.title
    subtitle = slide.placeholders[1]
    title.text = "蛋白质结构预测"
    subtitle.text = "AlphaFold2 实战教程"

    # 内容页 1
    slide_layout = prs.slide_layouts[1]
    slide = prs.slides.add_slide(slide_layout)
    title = slide.shapes.title
    title.text = "AlphaFold2 简介"
    content = slide.placeholders[1]
    tf = content.text_frame
    tf.text = "• 基于深度学习的蛋白质结构预测"
    p = tf.add_paragraph()
    p.text = "• 精度达到实验水平（< 1.5Å）"
    p = tf.add_paragraph()
    p.text = "• 输入：氨基酸序列"
    p = tf.add_paragraph()
    p.text = "• 输出：3D 结构模型（PDB 格式）"

    # 内容页 2
    slide = prs.slides.add_slide(slide_layout)
    title = slide.shapes.title
    title.text = "安装与运行"
    content = slide.placeholders[1]
    tf = content.text_frame
    tf.text = "1. 安装依赖"
    p = tf.add_paragraph()
    p.text = "   git clone https://github.com/deepmind/alphafold"
    p = tf.add_paragraph()
    p.text = "2. 运行预测"
    p = tf.add_paragraph()
    p.text = "   python3 run_alphafold.py --fasta_paths=target.fasta"

    pptx_path = os.path.join(test_dir, 'protein_structure.pptx')
    prs.save(pptx_path)
    print(f"✓ 创建 PPT 文档: {pptx_path}")

    # 4. 创建 HTML 文档
    html_content = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>空间转录组学分析</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
        h1 { color: #2c5282; }
        h2 { color: #2d3748; margin-top: 30px; }
        code { background: #f7fafc; padding: 2px 6px; border-radius: 4px; }
        pre { background: #f7fafc; padding: 15px; border-radius: 8px; overflow-x: auto; }
    </style>
</head>
<body>
    <h1>空间转录组学数据分析</h1>
    <p>空间转录组技术可以在保留组织空间信息的同时检测基因表达。</p>

    <h2>1. 技术平台</h2>
    <p><strong>10x Visium:</strong> 分辨率 55μm，基于捕获探针</p>
    <p><strong>MERFISH:</strong> 单分子分辨率，荧光原位杂交</p>

    <h2>2. 数据加载</h2>
    <pre><code>library(Seurat)
visium <- Load10X_Spatial(
  data.dir = "path/to/data/",
  assay = "Spatial"
)</code></pre>

    <h2>3. 空间可视化</h2>
    <pre><code>SpatialPlot(visium,
  features = c("Gene1", "Gene2"),
  alpha = c(0.1, 1)
)</code></pre>
</body>
</html>"""

    html_path = os.path.join(test_dir, 'spatial_transcriptomics.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"✓ 创建 HTML 文档: {html_path}")

    # 5. 创建 Markdown 文档
    md_content = """# RNA-seq 差异表达分析

## 实验设计

### 样本分组
- 对照组: Control (n=3)
- 处理组: Treatment (n=3)

### 测序策略
- 测序平台: Illumina NovaSeq 6000
- 读长: PE150
- 测序深度: 30M reads/sample

## 数据分析流程

### 1. 质控
```bash
fastqc *.fastq.gz -o qc_reports/
```

### 2. 比对
```bash
hisat2 -x genome_index -1 R1.fastq -2 R2.fastq -S output.sam
```

### 3. 差异分析
```r
library(DESeq2)
dds <- DESeqDataSetFromMatrix(countData, colData, ~condition)
dds <- DESeq(dds)
res <- results(dds)
```

## 结果解读
- |log2FC| > 1 且 padj < 0.05 为显著差异基因
- 进行 GO 和 KEGG 富集分析
"""

    md_path = os.path.join(test_dir, 'rnaseq_analysis.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"✓ 创建 Markdown 文档: {md_path}")

    return {
        'word': word_path,
        'pdf': pdf_path,
        'ppt': pptx_path,
        'html': html_path,
        'markdown': md_path
    }

def create_multiformat_toolkits():
    """创建多格式工具包"""
    app = create_app()
    with app.app_context():
        author = User.query.first()
        upload_folder = app.config["UPLOAD_FOLDER"]
        test_files = create_test_files()

        # 创建包含多种格式的工具包
        toolkits_data = [
            {
                'title': '多格式测试：单细胞分析完整教程',
                'summary': '测试各种文件格式的上传和展示效果',
                'description': '本工具包包含 Word、PDF、PPT、HTML、Markdown 等多种格式的课程资料',
                'category': '测试',
                'level': '入门',
                'lessons': [
                    {'title': '实验指南 (Word)', 'file': test_files['word'], 'type': 'word'},
                    {'title': '实验流程 (PDF)', 'file': test_files['pdf'], 'type': 'pdf'},
                    {'title': '技术介绍 (PPT)', 'file': test_files['ppt'], 'type': 'ppt'},
                    {'title': '数据分析 (HTML)', 'file': test_files['html'], 'type': 'html'},
                    {'title': '操作手册 (Markdown)', 'file': test_files['markdown'], 'type': 'markdown'},
                ]
            }
        ]

        for tk_data in toolkits_data:
            toolkit = Toolkit(
                title=tk_data['title'],
                slug=tk_data['title'][:30].replace(' ', '-').lower(),
                summary=tk_data['summary'],
                description=tk_data['description'],
                category=tk_data['category'],
                level=tk_data['level'],
                price=0,
                author_id=author.id,
                status='published',
                featured=True
            )
            db.session.add(toolkit)
            db.session.flush()

            for i, lesson_data in enumerate(tk_data['lessons']):
                file_path = lesson_data['file']

                # 创建 FileStorage 对象
                with open(file_path, 'rb') as f:
                    filename = os.path.basename(file_path)
                    file_storage = FileStorage(
                        stream=f,
                        filename=filename,
                        content_type='application/octet-stream'
                    )

                    # 保存原始文件
                    raw_path = save_raw_file(file_storage, upload_folder, lesson_data['title'])

                    # 转换文档 - 使用保存后的文件路径
                    content_type = detect_content_type(filename)
                    converted_html = convert_document(raw_path, content_type)

                    lesson = Lesson(
                        toolkit_id=toolkit.id,
                        title=lesson_data['title'],
                        content=converted_html,
                        content_type=content_type,
                        raw_file_path=raw_path,
                        duration=30,
                        sort_order=i,
                        is_preview=(i == 0)
                    )
                    db.session.add(lesson)

        db.session.commit()
        print(f"\n✓ 已创建多格式测试工具包")
        print(f"  - 包含 5 种不同格式的课程")
        print(f"  - Word: 1 个")
        print(f"  - PDF: 1 个")
        print(f"  - PPT: 1 个")
        print(f"  - HTML: 1 个")
        print(f"  - Markdown: 1 个")

if __name__ == '__main__':
    create_multiformat_toolkits()
