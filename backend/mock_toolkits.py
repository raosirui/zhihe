"""Create mock toolkits with various document formats."""
import os
import sys
import tempfile
from pathlib import Path

# Add backend to path
sys.path.insert(0, '/root/workspace/zhihe/backend')
os.chdir('/root/workspace/zhihe/backend')

# Load environment
from dotenv import load_dotenv
load_dotenv('/etc/zhihe/backend.env')

from app import create_app
from models import db, User, Toolkit, Lesson
from werkzeug.security import generate_password_hash
from document_converter import convert_document, save_raw_file

def create_mock_users(admin_password, user1_password, user2_password):
    """Create test users."""
    users = []

    # Admin user
    admin = User(
        username='admin',
        password_hash=generate_password_hash(admin_password),
        nickname='系统管理员',
        role='admin',
        bio='平台管理员'
    )
    db.session.add(admin)
    users.append(admin)

    # Regular user 1
    user1 = User(
        username='zhangsan',
        password_hash=generate_password_hash(user1_password),
        nickname='张三',
        role='user',
        bio='生物信息学研究员，专注单细胞分析'
    )
    db.session.add(user1)
    users.append(user1)

    # Regular user 2
    user2 = User(
        username='lisi',
        password_hash=generate_password_hash(user2_password),
        nickname='李四',
        role='user',
        bio='结构生物学博士后，蛋白质结构预测方向'
    )
    db.session.add(user2)
    users.append(user2)

    db.session.commit()
    print(f"Created {len(users)} users")
    return users

def create_mock_toolkit(user, title, summary, description, category, level, price, lessons_data):
    """Create a toolkit with lessons."""
    toolkit = Toolkit(
        title=title,
        slug=title[:20].replace(' ', '-').lower(),
        summary=summary,
        description=description,
        category=category,
        level=level,
        price=price,
        author_id=user.id,
        status='published',
        featured=False,
        cover=''
    )
    db.session.add(toolkit)
    db.session.flush()

    # Create lessons
    for i, lesson_data in enumerate(lessons_data):
        lesson = Lesson(
            toolkit_id=toolkit.id,
            title=lesson_data['title'],
            content=lesson_data.get('content', ''),
            content_type=lesson_data.get('content_type', 'markdown'),
            duration=lesson_data.get('duration', 10),
            sort_order=i,
            is_preview=(i == 0),  # First lesson is preview
            image=''
        )

        # If there's a document file, convert it
        if 'document_content' in lesson_data:
            # Create a temporary file
            ext = lesson_data.get('file_ext', '.md')
            with tempfile.NamedTemporaryFile(mode='w', suffix=ext, delete=False, encoding='utf-8') as f:
                f.write(lesson_data['document_content'])
                temp_path = f.name

            try:
                # Save raw file
                upload_folder = os.environ.get('UPLOAD_FOLDER', '/root/workspace/zhihe/backend/uploads')
                raw_path = save_raw_file(open(temp_path, 'rb'), upload_folder, lesson.id)
                lesson.raw_file_path = raw_path

                # Convert document
                converted_html = convert_document(raw_path, lesson.content_type)
                lesson.converted_html = converted_html

                # Set content placeholder
                lesson.content = f"[Document: {lesson_data.get('filename', 'document')}{ext}]"
            except Exception as e:
                print(f"Warning: Failed to convert document for lesson {lesson.title}: {e}")
                lesson.converted_html = f"<p>文档转换失败：{str(e)}</p>"
            finally:
                os.unlink(temp_path)

        db.session.add(lesson)

    db.session.commit()
    print(f"Created toolkit: {title} with {len(lessons_data)} lessons")
    return toolkit

def main():
    if os.environ.get('APP_ENV') == 'production' and os.environ.get('ALLOW_PRODUCTION_MOCK_SEED') != 'YES':
        raise SystemExit('Production mock seeding requires ALLOW_PRODUCTION_MOCK_SEED=YES')
    keys = ('MOCK_ADMIN_PASSWORD', 'MOCK_ZHANGSAN_PASSWORD', 'MOCK_LISI_PASSWORD')
    passwords = [os.environ.get(key) for key in keys]
    if any(not password or len(password) < 16 for password in passwords) or len(set(passwords)) != 3:
        raise SystemExit('Set three distinct mock-account passwords of at least 16 characters via environment variables')
    app = create_app()
    with app.app_context():
        # Create users
        users = create_mock_users(*passwords)
        admin, zhangsan, lisi = users

        # Toolkit 1: Markdown tutorial (by zhangsan)
        toolkit1 = create_mock_toolkit(
            user=zhangsan,
            title='单细胞 RNA-seq 标准分析工作流',
            summary='从零开始学习单细胞转录组分析，涵盖质控、聚类、差异表达全流程',
            description='''# 课程介绍

本课程面向生物信息学初学者，系统讲解单细胞 RNA-seq 数据分析的标准流程。

## 学习目标
- 掌握单细胞数据质控方法
- 理解降维聚类原理
- 学会差异表达分析
- 能够独立完成完整分析流程

## 前置知识
- 基础的 R 语言编程
- 基本的统计学概念
- 分子生物学基础

## 运行环境
- R 4.0+
- Seurat 4.0+
- 内存 ≥ 16GB''',
            category='单细胞',
            level='入门',
            price=0,
            lessons_data=[
                {
                    'title': '环境准备与数据下载',
                    'content': '''# 环境准备

## 安装必要的 R 包

```r
install.packages("Seurat")
install.packages("ggplot2")
install.packages("dplyr")
```

## 下载示例数据

从 GEO 数据库下载 PBMC 数据集：

```r
library(Seurat)
pbmc.data <- Read10X(data.dir = "filtered_gene_bc_matrices/hg19/")
```

## 创建 Seurat 对象

```r
pbmc <- CreateSeuratObject(counts = pbmc.data, project = "pbmc_tutorial")
```
''',
                    'duration': 15
                },
                {
                    'title': '质量控制与过滤',
                    'content': '''# 质量控制

## 检查线粒体基因比例

```r
pbmc[["percent.mt"]] <- PercentageFeatureSet(pbmc, pattern = "^MT-")
```

## 可视化质控指标

```r
VlnPlot(pbmc, features = c("nFeature_RNA", "nCount_RNA", "percent.mt"), ncol = 3)
```

## 过滤细胞

```r
pbmc <- subset(pbmc, subset = nFeature_RNA > 200 & nFeature_RNA < 2500 & percent.mt < 5)
```
''',
                    'duration': 20
                },
                {
                    'title': '标准化与降维',
                    'content': '''# 数据标准化

## LogNormalize 标准化

```r
pbmc <- NormalizeData(pbmc, normalization.method = "LogNormalize", scale.factor = 10000)
```

## 寻找高变基因

```r
pbmc <- FindVariableFeatures(pbmc, selection.method = "vst", nfeatures = 2000)
```

## PCA 降维

```r
pbmc <- ScaleData(pbmc)
pbmc <- RunPCA(pbmc, features = VariableFeatures(object = pbmc))
```

## UMAP 可视化

```r
pbmc <- RunUMAP(pbmc, dims = 1:10)
DimPlot(pbmc, reduction = "umap")
```
''',
                    'duration': 25
                }
            ]
        )

        # Toolkit 2: Word document tutorial (by lisi)
        toolkit2 = create_mock_toolkit(
            user=lisi,
            title='蛋白质结构预测实战指南',
            summary='使用 AlphaFold2 和 Rosetta 进行蛋白质结构预测的完整教程',
            description='本课程详细介绍蛋白质结构预测的主流方法，包括 AlphaFold2、Rosetta 等工具的使用。',
            category='结构生物学',
            level='进阶',
            price=49.9,
            lessons_data=[
                {
                    'title': '蛋白质结构基础',
                    'document_content': '''# 蛋白质结构基础

## 蛋白质结构层次

### 一级结构
氨基酸序列，由肽键连接。

### 二级结构
局部空间结构，主要包括：
- α-螺旋 (α-helix)
- β-折叠 (β-sheet)
- β-转角 (β-turn)
- 无规卷曲 (random coil)

### 三级结构
整条多肽链的三维空间排布，由以下作用力维持：
- 氢键
- 疏水作用
- 离子键
- 范德华力
- 二硫键

### 四级结构
多条多肽链（亚基）的空间排布。

## 蛋白质结构测定方法

1. X射线晶体学
2. 核磁共振 (NMR)
3. 冷冻电镜 (Cryo-EM)
4. 计算预测方法

## 结构预测的重要性

蛋白质结构决定功能，结构预测是理解蛋白质功能的关键。
''',
                    'content_type': 'markdown',
                    'file_ext': '.md',
                    'filename': 'protein_structure_basics',
                    'duration': 30
                },
                {
                    'title': 'AlphaFold2 原理与安装',
                    'document_content': '''# AlphaFold2 原理与安装

## AlphaFold2 的核心创新

AlphaFold2 在 CASP14 中取得了突破性进展，其核心创新包括：

### 1. ESMFold 架构
使用端到端的深度学习架构，直接从氨基酸序列预测三维结构。

### 2. Attention 机制
引入多头注意力机制，捕捉残基间的长程相互作用。

### 3. 结构模块
使用几何深度学习预测原子坐标。

## 安装要求

### 硬件要求
- GPU: NVIDIA V100 或更高（推荐 A100）
- 内存: ≥ 32GB
- 存储: ≥ 1TB SSD

### 软件要求
- CUDA 11.3+
- Python 3.8+
- PyTorch 1.12+

## 安装步骤

```bash
# 克隆仓库
git clone https://github.com/deepmind/alphafold.git
cd alphafold

# 创建 conda 环境
conda env create -f environment.yml
conda activate alphafold

# 下载模型参数
python3 download_alphafold_params.py

# 安装
pip install -e .
```

## 运行预测

```bash
python3 run_alphafold.py \\
  --fasta_paths=target.fasta \\
  --data_dir=/data/alphafold \\
  --output_dir=/results
```
''',
                    'content_type': 'markdown',
                    'file_ext': '.md',
                    'filename': 'alphafold2_tutorial',
                    'duration': 45
                }
            ]
        )

        # Toolkit 3: HTML tutorial (by admin)
        toolkit3 = create_mock_toolkit(
            user=admin,
            title='生物信息学数据分析最佳实践',
            summary='涵盖基因组、转录组、蛋白质组学数据分析的标准流程和注意事项',
            description='本课程总结生物信息学数据分析中的常见问题和最佳实践。',
            category='生信分析',
            level='专业',
            price=99.0,
            lessons_data=[
                {
                    'title': '数据分析流程概览',
                    'content': '''# 生物信息学数据分析流程

## 标准流程

1. **实验设计**
   - 明确研究问题
   - 确定样本量和重复数
   - 选择合适的测序平台

2. **数据获取**
   - 原始数据下载
   - 数据质量评估
   - 数据预处理

3. **数据分析**
   - 质控与过滤
   - 比对/定量
   - 统计分析
   - 功能注释

4. **结果验证**
   - 实验验证
   - 独立数据集验证
   - 文献支持

## 常见问题

### 批次效应
- 使用 ComBat 或 limma 校正
- 实验设计时随机化

### 多重检验校正
- 使用 FDR (Benjamini-Hochberg)
- 设定合理的阈值

### 数据标准化
- 根据不同平台选择合适方法
- 注意对数转换的影响
''',
                    'duration': 40
                }
            ]
        )

        # Toolkit 4: PDF-style tutorial (by zhangsan)
        toolkit4 = create_mock_toolkit(
            user=zhangsan,
            title='空间转录组学入门到精通',
            summary='系统学习空间转录组技术原理、数据分析方法和应用场景',
            description='空间转录组学是近年来的热门技术，本课程全面介绍该领域。',
            category='空间组学',
            level='入门',
            price=29.9,
            lessons_data=[
                {
                    'title': '空间转录组技术原理',
                    'content': '''# 空间转录组技术原理

## 主要技术平台

### 1. 10x Genomics Visium
- 基于捕获探针的空间条形码技术
- 分辨率：55μm（约 1-10 个细胞）
- 适用于 FFPE 和冷冻样本

### 2. Slide-seq
- 基于 beads 的高分辨率技术
- 分辨率：10μm（单细胞水平）
- 需要定制芯片

### 3. MERFISH
- 基于荧光原位杂交
- 可检测数百到数千个基因
- 单分子分辨率

### 4. seqFISH+
- 高通量 FISH 技术
- 可检测 >10,000 个基因
- 亚细胞分辨率

## 技术选择建议

| 需求 | 推荐技术 |
|------|----------|
| 高通量筛查 | Visium |
| 单细胞分辨率 | Slide-seq |
| 特定基因验证 | MERFISH |
| 全转录组单分子 | seqFISH+ |
''',
                    'duration': 35
                }
            ]
        )

        # Toolkit 5: Gene editing tutorial (by lisi)
        toolkit5 = create_mock_toolkit(
            user=lisi,
            title='CRISPR-Cas9 基因编辑实验指南',
            summary='从设计到验证的完整 CRISPR 实验流程，包含常见问题解决方案',
            description='详细的 CRISPR-Cas9 实验操作指南。',
            category='基因编辑',
            level='进阶',
            price=59.0,
            lessons_data=[
                {
                    'title': 'CRISPR 系统原理',
                    'content': '''# CRISPR-Cas9 系统原理

## 系统组成

### Cas9 蛋白
- 核酸内切酶
- 识别 PAM 序列 (NGG)
- 在 guide RNA 引导下切割靶 DNA

### Guide RNA (gRNA)
- 20nt 引导序列
- scaffold 结构
- 与 Cas9 形成复合物

## 工作机制

1. gRNA 与 Cas9 结合形成 RNP 复合物
2. 复合物扫描 DNA，寻找 PAM 序列
3. gRNA 与靶序列互补配对
4. Cas9 切割双链 DNA
5. 细胞修复机制（NHEJ 或 HDR）

## 应用领域

- 基因敲除
- 基因敲入
- 基因调控
- 表观遗传编辑
- 碱基编辑
''',
                    'duration': 25
                }
            ]
        )

        print("\n=== Mock Data Creation Complete ===")
        print(f"Users: {len(users)}")
        print(f"Toolkits: 5")
        print(f"\nToolkits created:")
        print(f"  1. 单细胞 RNA-seq 标准分析工作流 (免费)")
        print(f"  2. 蛋白质结构预测实战指南 (¥49.9)")
        print(f"  3. 生物信息学数据分析最佳实践 (¥99.0)")
        print(f"  4. 空间转录组学入门到精通 (¥29.9)")
        print(f"  5. CRISPR-Cas9 基因编辑实验指南 (¥59.0)")

if __name__ == '__main__':
    main()
