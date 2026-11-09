"""创建 10 个超详细的专业工具包"""
import os
import sys
sys.path.insert(0, '/root/workspace/zhihe/backend')
os.chdir('/root/workspace/zhihe/backend')

from dotenv import load_dotenv
load_dotenv('/etc/zhihe/backend.env')

from app import create_app
from models import db, User, Toolkit, Lesson

def create_comprehensive_toolkits():
    app = create_app()
    with app.app_context():
        author = User.query.first()

        # 工具包 1: 单细胞转录组学完整分析流程
        toolkit1 = Toolkit(
            title='单细胞转录组学完整分析流程：从原始数据到生物学发现（超级详细版）',
            slug='single-cell-rna-seq-complete-analysis',
            summary='本教程提供 10x Genomics 平台单细胞 RNA-seq 数据分析的完整工作流程，从实验设计、数据预处理、质量控制、降维聚类、细胞类型注释、差异表达分析到轨迹推断，涵盖所有分析步骤和最佳实践',
            description='''# 课程概述

本课程是单细胞 RNA-seq 数据分析的完整指南，基于 10x Genomics 平台和 Seurat R 包。

## 学习目标

完成本课程后，你将能够：
- 设计单细胞实验方案并选择合适的技术平台
- 下载和处理 10x Genomics 原始数据
- 执行严格的数据质量控制和过滤
- 进行数据标准化和高变基因选择
- 执行降维分析（PCA、UMAP、tSNE）
- 进行聚类分析和细胞类型注释
- 执行差异表达分析和功能富集
- 进行拟时序分析和细胞轨迹推断
- 生成可发表的图表和分析报告

## 适用人群

- 生物信息学研究生和博士后
- 分子生物学研究人员
- 医学研究者
- 对单细胞分析感兴趣的生命科学从业者

## 前置要求

- R 语言基础（数据框操作、函数编写、包安装）
- 基本的统计学知识（假设检验、多重检验校正）
- Linux 命令行操作经验
- 分子生物学基础（基因表达、细胞类型）

## 运行环境要求

- **操作系统**: Linux (Ubuntu 20.04+, CentOS 7+) 或 macOS
- **R 版本**: 4.3.0 或更高
- **内存**: 至少 32GB（推荐 64GB 以上）
- **存储**: 至少 100GB 可用空间
- **CPU**: 多核处理器（推荐 8 核以上）

## 课程结构

本课程包含 10 个主要章节，每个章节都有详细的理论讲解、代码示例和实操练习。

## 数据说明

本课程使用公开的 PBMC（外周血单核细胞）数据集作为示例，数据来源于 10x Genomics 官网。''',
            category='单细胞',
            level='进阶',
            price=0,
            author_id=author.id,
            status='published',
            featured=False
        )
        db.session.add(toolkit1)
        db.session.flush()

        lessons_data = [
            {
                'title': '第一章：单细胞 RNA-seq 技术原理与实验设计',
                'content': '''# 第一章：单细胞 RNA-seq 技术原理与实验设计

## 1.1 单细胞转录组学概述

### 1.1.1 什么是单细胞 RNA-seq？

单细胞 RNA 测序（scRNA-seq）是一种高通量测序技术，能够在单个细胞水平上分析基因表达谱。与传统的批量 RNA-seq 不同，scRNA-seq 可以揭示细胞间的异质性，发现新的细胞亚群，并追踪细胞发育轨迹。

**核心优势：**
- **细胞异质性解析**：识别组织中的不同细胞类型和状态
- **稀有细胞检测**：发现占比极小但功能重要的细胞群
- **发育轨迹重建**：追踪细胞分化和发育过程
- **疾病机制研究**：揭示疾病状态下的细胞变化

### 1.1.2 单细胞技术的发展历程

**2009年**：汤富酬团队发表第一篇单细胞转录组文章（Smart-seq）
**2013年**：Drop-seq 和 inDrop 技术出现，实现高通量单细胞分析
**2017年**：10x Genomics Chromium 平台商业化，成为主流技术
**2019年**：单细胞多组学技术出现（CITE-seq、ATAC-seq + RNA-seq）
**2021年**：空间转录组技术成熟（Visium、MERFISH）

### 1.1.3 主要技术平台对比

| 平台 | 原理 | 通量 | 成本 | 优势 | 劣势 |
|------|------|------|------|------|------|
| **10x Chromium** | 微流控 + 油滴 | 1k-10k cells/run | ¥5000-10000/sample | 高通量、标准化 | 成本较高 |
| **Smart-seq2** | 板式分选 | 96-384 cells/run | ¥2000-5000/sample | 全长转录本、高灵敏度 | 通量低 |
| **Drop-seq** | 油滴 | 10k-100k cells/run | ¥1000-3000/sample | 超高通量 | 需要自建平台 |
| **inDrop** | 水凝胶微滴 | 10k-50k cells/run | ¥2000-4000/sample | 成本较低 | 灵敏度较低 |
| **CEL-seq2** | 板式 + 条形码 | 384-1536 cells/run | ¥1500-3000/sample | 灵敏度高 | 通量有限 |

### 1.1.4 10x Genomics 技术原理

**核心步骤：**

1. **细胞悬液制备**：将组织消化成单细胞悬液
2. **微流控封装**：细胞与凝胶微珠共同封装在油滴中
3. **细胞裂解**：在油滴内裂解细胞，释放 mRNA
4. **条形码标记**：每个微珠携带独特的 10x 条形码（16bp）
5. **逆转录**：mRNA 逆转录为 cDNA，加上 UMIs（Unique Molecular Identifiers）
6. **文库构建**：cDNA 片段化，加上测序接头
7. **高通量测序**：Illumina 平台测序

**数据结构：**
- **10x Barcode**：识别细胞来源（~800,000 种）
- **UMI**：识别原始 mRNA 分子（4^10 = 1,048,576 种）
- **Read**：测序读段（通常 90bp）

## 1.2 实验设计要点

### 1.2.1 样本量计算

**生物学重复：**
- 最少 3 个生物学重复（统计分析要求）
- 推荐 5-6 个重复（提高统计功效）
- 考虑批次效应，随机化处理

**细胞数确定：**
```r
# 基于预期效应量计算所需细胞数
library(pwr)

# 参数设置
effect_size <- 0.5  # Cohen's d，中等效应量
power <- 0.8        # 统计功效 80%
alpha <- 0.05       # 显著性水平

# 计算样本量
sample_result <- pwr.t.test(d=effect_size, power=power, sig.level=alpha)
cells_needed <- ceiling(sample_result$n)

cat("每组所需细胞数:", cells_needed, "\\n")
# 通常每组需要 500-5000 个细胞
```

**经验法则：**
- 细胞类型鉴定：每组 1000-5000 个细胞
- 差异表达分析：每组 3000-10000 个细胞
- 稀有细胞检测：每组 10000+ 个细胞

### 1.2.2 批次效应控制

**批次效应来源：**
- 不同实验日期
- 不同操作人员
- 不同试剂批次
- 不同测序泳道

**控制策略：**

1. **实验设计层面：**
```
推荐设计：
- 随机化样本处理顺序
- 每个批次包含所有实验组
- 使用技术重复评估批次效应
```

2. **细胞条形码标记（Cell Hashing）：**
```r
# 使用 Hashtag Oligos (HTOs) 标记不同样本
# 可以在同一批次中混合多个样本
library(Seurat)

# 创建 HTO 对象
hto_counts <- Read10X(data.dir = "hto_data/")
hto <- CreateSeuratObject(counts = hto_counts)

#  demultiplexing
hto <- HTODemultiplex(hto, positive.quantile = 0.99)
```

3. **计算校正：**
```r
# 使用 Harmony 或 Seurat Integration 校正批次效应
library(harmony)

# Harmony 校正
pbmc <- RunHarmony(pbmc, group.by.vars = "batch")

# 或 Seurat Integration
anchors <- FindIntegrationAnchors(object.list = list(pbmc1, pbmc2), dims = 1:30)
pbmc.integrated <- IntegrateData(anchorset = anchors, dims = 1:30)
```

### 1.2.3 样本制备注意事项

**组织消化：**
```bash
# 推荐的消化酶组合
# 实体组织：胶原酶 IV + DNase I
# 肿瘤组织：胶原酶 IV + 透明质酸酶 + DNase I
# 脑组织：木瓜蛋白酶 + DNase I

# 消化时间控制
# 37°C 孵育 30-60 分钟
# 每 10 分钟轻轻吹打混匀
```

**细胞活力要求：**
- 活力 > 80%（台盼蓝染色检测）
- 活力 < 70% 的样本不建议上机
- 死细胞会释放 RNA，造成环境 RNA 污染

**细胞浓度：**
- 推荐浓度：700-1200 cells/μL
- 浓度过高：多个细胞进入同一油滴（doublets）
- 浓度过低：细胞捕获效率低

### 1.2.4 测序深度设计

**测序深度建议：**

| 应用 | 推荐深度 | 说明 |
|------|----------|------|
| 细胞类型鉴定 | 20,000-50,000 reads/cell | 足够检测主要基因 |
| 差异表达分析 | 50,000-100,000 reads/cell | 需要检测低表达基因 |
| 可变剪接分析 | 100,000+ reads/cell | 需要覆盖全长转录本 |

**计算总数据量：**
```r
# 示例计算
cells_per_sample <- 5000
reads_per_cell <- 50000
samples <- 6

total_reads <- cells_per_sample * reads_per_cell * samples
total_gb <- total_reads * 150 / 1e9  # 假设 read length = 150bp

cat("总数据量:", round(total_gb, 2), "GB\\n")
```

## 1.3 数据获取与下载

### 1.3.1 从 GEO 下载数据

**GEO 数据库简介：**
- Gene Expression Omnibus (GEO) 是 NCBI 的公共数据仓库
- 存储高通量测序数据和芯片数据
- 提供数据检索和下载服务

**下载流程：**

1. **搜索数据集：**
```bash
# 访问 GEO 网站
# https://www.ncbi.nlm.nih.gov/geo/

# 搜索关键词
# "single cell RNA-seq" AND "10x Genomics" AND "human"

# 示例数据集：GSE123456
```

2. **使用 aspera 高速下载：**
```bash
# 安装 aspera connect
# https://www.ibm.com/aspera/connect/

# 下载命令
ascp -QT -l 300m -P33001 -i ~/.aspera/connect/etc/asperaweb_id_dsa.openssh era-fasp@fasp.sra.ebi.ac.uk:/vol1/fastq/SRR123456/SRR123456 .
```

3. **使用 wget 下载：**
```bash
# 下载 SRA 数据
wget -c https://sra-pub-src-1.s3.amazonaws.com/SRR123456/sample.fastq.gz.1

# 下载处理后的表达矩阵
wget -c https://ftp.ncbi.nlm.nih.gov/geo/series/GSE123nnn/GSE123456/suppl/GSE123456_processed_matrix.txt.gz
```

### 1.3.2 从 10x Genomics 官网下载

**10x 数据集页面：**
```bash
# 访问 10x 数据集网站
# https://www.10xgenomics.com/resources/datasets

# 示例：PBMC 数据集
wget https://cf.10xgenomics.com/samples/cell-exp/4.0.0/pbmc_10k_v3/pbmc_10k_v3_filtered_feature_bc_matrix.h5
```

### 1.3.3 数据格式说明

**10x 标准输出格式：**

```
sample_folder/
├── barcodes.tsv.gz      # 细胞条形码列表（每行一个 barcode）
├── features.tsv.gz      # 基因注释信息（gene_id, gene_name, feature_type）
├── matrix.mtx.gz        # 表达矩阵（Market Matrix 格式，稀疏矩阵）
├── web_summary.html     # 质控报告（HTML 格式）
├── metrics_summary.csv  # 质控指标汇总（CSV 格式）
└── raw_feature_bc_matrix/  # 原始矩阵（包含 intronic reads）
```

**文件格式详解：**

1. **barcodes.tsv.gz**：
```
AAACCTGAGAAACCAT-1
AAACCTGAGAAACCGC-1
AAACCTGAGAAACCTA-1
...
```

2. **features.tsv.gz**：
```
ENSG00000243485	MIR1302-2HG	Gene Expression
ENSG00000237613	FAM138A	Gene Expression
ENSG00000186092	OR4F5	Gene Expression
```

3. **matrix.mtx.gz**：
```
%%MatrixMarket matrix coordinate integer general
%barcode	gene	count
1	1	3
1	2	1
1	5	2
...
```

## 1.4 环境配置与软件安装

### 1.4.1 R 环境安装

**安装 R 4.3+：**
```bash
# Ubuntu
sudo apt-get update
sudo apt-get install -y r-base r-base-dev

# CentOS
sudo yum install -y R R-devel

# macOS (使用 Homebrew)
brew install R
```

**验证 R 版本：**
```bash
R --version
# 应该显示 R version 4.3.0 或更高
```

### 1.4.2 安装 Bioconductor 包

```r
# 安装 BiocManager
if (!requireNamespace("BiocManager", quietly = TRUE))
    install.packages("BiocManager")

# 设置 Bioconductor 镜像（中国用户）
options(BioC_mirror="https://mirrors.ustc.edu.cn/bioc/")

# 安装核心包
BiocManager::install(c(
    "Seurat",           # 单细胞分析核心包
    "SingleCellExperiment", # 单细胞数据结构
    "scran",           # 单细胞标准化
    "scater",          # 单细胞质控
    "Monocle3",        # 拟时序分析
    "clusterProfiler", # 功能富集
    "org.Hs.eg.db",    # 人类基因注释
    "harmony",         # 批次效应校正
    "DoubletFinder",   # 双细胞检测
    "sctransform"      # 数据标准化
))
```

### 1.4.3 安装 CRAN 包

```r
# 设置 CRAN 镜像（中国用户）
options(repos = c(CRAN = "https://mirrors.ustc.edu.cn/CRAN/"))

# 安装可视化包
install.packages(c(
    "ggplot2",      # 图形绘制
    "patchwork",    # 图形组合
    "cowplot",      # 图形排版
    "viridis",      # 颜色方案
    "RColorBrewer", # 配色方案
    "pheatmap",     # 热图
    "ComplexHeatmap" # 复杂热图
))

# 安装数据处理包
install.packages(c(
    "dplyr",        # 数据操作
    "tidyr",        # 数据整理
    "stringr",      # 字符串处理
    "purrr",        # 函数式编程
    "data.table",   # 快速数据处理
    "Matrix"        # 稀疏矩阵
))
```

### 1.4.4 加载数据

```r
# 加载必要的包
library(Seurat)
library(ggplot2)
library(dplyr)

# 读取 10x 数据
pbmc.data <- Read10X(data.dir = "path/to/pbmc_filtered_feature_bc_matrix/")

# 查看数据结构
str(pbmc.data)
# Formal class 'dgCMatrix' [package "Matrix"] with 6 slots
#   ..@ i       : int [1:2531050] 0 1 2 3 4 5 6 7 8 9 ...
#   ..@ p       : int [1:2701] 0 113 187 254 323 390 458 525 592 659 ...
#   ..@ Dim     : int [1:2] 32738 2700
#   ..@ Dimnames:List of 2
#   .. ..$ : chr [1:32738] "MIR1302-2HG" "FAM138A" "OR4F5" "MIR1302-2" ...
#   .. ..$ : chr [1:2700] "AAACCTGAGAAACCAT-1" "AAACCTGAGAAACCGC-1" ...

# 创建 Seurat 对象
pbmc <- CreateSeuratObject(
    counts = pbmc.data,
    project = "PBMC_Tutorial",
    min.cells = 3,      # 至少在 3 个细胞中表达
    min.features = 200  # 每个细胞至少检测 200 个基因
)

# 查看 Seurat 对象
pbmc
# An object of class Seurat
# 32738 features across 2700 samples within the 1 assay
# Active assay: RNA (32738 features, 0 variable features)
```

## 1.5 本章小结

本章介绍了单细胞 RNA-seq 的技术原理和实验设计要点：

**关键概念：**
- 单细胞测序可以揭示细胞异质性
- 10x Genomics 是目前最主流的技术平台
- 实验设计需要考虑样本量、批次效应和测序深度

**实践要点：**
- 细胞活力 > 80%
- 每组至少 3 个生物学重复
- 测序深度根据应用目的调整
- 使用 Cell Hashing 控制批次效应

**下一步：**
下一章将详细介绍数据质量控制和过滤的标准流程。''',
                'duration': 90
            },
            {
                'title': '第二章：数据质量控制与过滤完全指南',
                'content': '''# 第二章：数据质量控制与过滤完全指南

## 2.1 质量控制指标详解

### 2.1.1 nCount_RNA：UMI 总数

**定义：** 每个细胞检测到的 UMI（Unique Molecular Identifier）总数，反映细胞的总 mRNA 含量。

**生物学意义：**
- 低 nCount：可能是死细胞、空液滴或低质量细胞
- 高 nCount：可能是双细胞（doublet）或多细胞
- 异常高：可能是环境 RNA 污染

**计算方法：**
```r
# Seurat 自动计算
pbmc[["nCount_RNA"]]
# [1]  1234  2345  3456  4567  5678 ...

# 手动计算
nCount <- Matrix::colSums(pbmc@assays$RNA@counts)
```

**阈值设定：**
```r
# 基于中位数绝对偏差（MAD）
median_count <- median(pbmc$nCount_RNA)
mad_count <- mad(pbmc$nCount_RNA)

lower_bound <- median_count - 3 * mad_count
upper_bound <- median_count + 3 * mad_count

cat(sprintf("nCount 阈值: %d - %d\\n", lower_bound, upper_bound))
# 通常范围: 500 - 25000
```

### 2.1.2 nFeature_RNA：基因数

**定义：** 每个细胞检测到的基因数量（表达量 > 0 的基因数）。

**生物学意义：**
- 低 nFeature：细胞质量差或 mRNA 降解
- 高 nFeature：可能是双细胞
- 与 nCount 相关但不同（高表达基因 vs 基因多样性）

**阈值设定：**
```r
# 基于 MAD
median_features <- median(pbmc$nFeature_RNA)
mad_features <- mad(pbmc$nFeature_RNA)

lower_bound <- median_features - 3 * mad_features
upper_bound <- median_features + 3 * mad_features

cat(sprintf("nFeature 阈值: %d - %d\\n", lower_bound, upper_bound))
# 通常范围: 200 - 2500
```

### 2.1.3 percent.mt：线粒体基因比例

**定义：** 线粒体基因表达量占总表达量的百分比。

**生物学意义：**
- 高 percent.mt：细胞膜破损，细胞质 RNA 泄漏，只剩线粒体 RNA
- 通常表示死细胞或受损细胞
- 不同组织类型阈值不同

**计算方法：**
```r
# Seurat 内置函数
pbmc[["percent.mt"]] <- PercentageFeatureSet(
    pbmc,
    pattern = "^MT-"  # 人类线粒体基因前缀
)

# 小鼠数据使用 "^mt-"
# 线粒体基因在基因组中的位置
mito_genes <- c("MT-ND1", "MT-ND2", "MT-CO1", "MT-CO2", "MT-ATP6", "MT-ATP8",
                "MT-CO3", "MT-ND3", "MT-ND4L", "MT-ND4", "MT-ND5", "MT-ND6",
                "MT-CYB", "MT-RNR1", "MT-RNR2", "MT-TA", "MT-TC", "MT-TD")
```

**阈值设定：**
```r
# 不同组织类型的阈值
# PBMC: < 5-10%
# 实体组织: < 15-20%
# 肿瘤组织: < 25%

mt_threshold <- 20  # 根据组织类型调整
```

### 2.1.4 其他 QC 指标

**percent.ribo：核糖体基因比例**
```r
# 核糖体基因
ribo_genes <- grep("^RP[SL]", rownames(pbmc), value = TRUE)
pbmc[["percent.ribo"]] <- PercentageFeatureSet(pbmc, features = ribo_genes)
```

**doublet.score：双细胞评分**
```r
# 使用 DoubletFinder
library(DoubletFinder)

# 计算双细胞评分
pbmc <- doubletFinder_v3(
    pbmc,
    PCs = 1:10,
    pN = 0.25,
    pK = 0.09,
    nExp = round(0.075 * ncol(pbmc))
)
```

## 2.2 可视化质控

### 2.2.1 小提琴图（Violin Plot）

```r
# 基本小提琴图
p1 <- VlnPlot(pbmc, features = "nCount_RNA", pt.size = 0.1) +
    geom_hline(yintercept = c(500, 25000), linetype = "dashed", color = "red") +
    ggtitle("UMI Count Distribution")

p2 <- VlnPlot(pbmc, features = "nFeature_RNA", pt.size = 0.1) +
    geom_hline(yintercept = c(200, 2500), linetype = "dashed", color = "red") +
    ggtitle("Gene Count Distribution")

p3 <- VlnPlot(pbmc, features = "percent.mt", pt.size = 0.1) +
    geom_hline(yintercept = 20, linetype = "dashed", color = "red") +
    ggtitle("Mitochondrial Percentage")

# 组合图形
library(patchwork)
qc_plot <- p1 + p2 + p3
ggsave("qc_violin_plots.png", qc_plot, width = 15, height = 5, dpi = 300)
```

### 2.2.2 散点图（Scatter Plot）

```r
# nCount vs nFeature
scatter1 <- FeatureScatter(
    pbmc,
    feature1 = "nCount_RNA",
    feature2 = "nFeature_RNA"
) +
    geom_hline(yintercept = c(200, 2500), linetype = "dashed", color = "red") +
    geom_vline(xintercept = c(500, 25000), linetype = "dashed", color = "red") +
    ggtitle("nCount vs nFeature")

# nCount vs percent.mt
scatter2 <- FeatureScatter(
    pbmc,
    feature1 = "nCount_RNA",
    feature2 = "percent.mt"
) +
    geom_hline(yintercept = 20, linetype = "dashed", color = "red") +
    ggtitle("nCount vs Mito %")

# 组合
scatter_plot <- scatter1 + scatter2
ggsave("qc_scatter_plots.png", scatter_plot, width = 12, height = 6, dpi = 300)
```

### 2.2.3 密度图（Density Plot）

```r
# 使用 ggplot2 绘制密度图
library(ggplot2)

# nFeature 密度图
density_plot <- ggplot(pbmc@meta.data, aes(x = nFeature_RNA)) +
    geom_density(fill = "lightblue", alpha = 0.7) +
    geom_vline(xintercept = c(200, 2500), linetype = "dashed", color = "red") +
    theme_minimal() +
    labs(
        title = "Gene Count Density Distribution",
        x = "Number of Genes",
        y = "Density"
    )

ggsave("qc_density_plot.png", density_plot, width = 8, height = 6, dpi = 300)
```

## 2.3 细胞过滤策略

### 2.3.1 标准过滤流程

```r
# 打印过滤前细胞数
cat(sprintf("过滤前细胞数: %d\\n", ncol(pbmc)))

# 应用过滤条件
pbmc_filtered <- subset(
    pbmc,
    subset = nFeature_RNA > 200 &     # 至少 200 个基因
             nFeature_RNA < 2500 &     # 最多 2500 个基因
             nCount_RNA > 500 &        # 至少 500 个 UMI
             nCount_RNA < 25000 &      # 最多 25000 个 UMI
             percent.mt < 20           # 线粒体比例 < 20%
)

# 打印过滤后细胞数
cat(sprintf("过滤后细胞数: %d\\n", ncol(pbmc_filtered)))
cat(sprintf("保留比例: %.1f%%\\n", 100 * ncol(pbmc_filtered) / ncol(pbmc)))

# 通常保留 70-90% 的细胞
```

### 2.3.2 双细胞检测与去除

**DoubletFinder 详细流程：**

```r
library(DoubletFinder)

# 1. 数据预处理
pbmc_filtered <- NormalizeData(pbmc_filtered)
pbmc_filtered <- FindVariableFeatures(pbmc_filtered, selection.method = "vst", nfeatures = 2000)
pbmc_filtered <- ScaleData(pbmc_filtered)
pbmc_filtered <- RunPCA(pbmc_filtered, npcs = 30)

# 2. 参数扫描
# pK 值扫描
sweep.res.list <- paramSweep_v3(pbmc_filtered, PCs = 1:30, sct = FALSE)
sweep.stats <- summarizeSweep(sweep.res.list, GT = FALSE)
bcmvn <- find.pK(sweep.stats)

# 选择最优 pK
optimal_pK <- as.numeric(as.character(bcmvn$pK[which.max(bcmvn$BCmetric)]))
cat(sprintf("最优 pK: %s\\n", optimal_pK))

# 3. 计算预期双细胞数
# 10x 官方数据：约 5-10% 的双细胞率
homotypic.prop <- modelHomotypic(annotations)  # 如果有细胞类型注释
nExp_poi <- round(0.075 * ncol(pbmc_filtered))  # 预期双细胞数
nExp_poi <- round(nExp_poi * 2)  # 保守估计

# 4. 运行 DoubletFinder
pbmc_filtered <- doubletFinder_v3(
    pbmc_filtered,
    PCs = 1:30,
    pN = 0.25,        # 模拟双细胞比例
    pK = optimal_pK,  # 最优 pK
    nExp = nExp_poi,  # 预期双细胞数
    reuse.pCA = FALSE,
    sct = FALSE
)

# 5. 查看结果
doublet_col <- grep("DF.classifications", colnames(pbmc_filtered@meta.data), value = TRUE)[1]
table(pbmc_filtered@meta.data[[doublet_col]])
# Singlet  Doublet
#   2400      150

# 6. 去除双细胞
pbmc_filtered <- pbmc_filtered[, pbmc_filtered@meta.data[[doublet_col]] == "Singlet"]
```

### 2.3.3 基因过滤

```r
# 过滤低表达基因
# 至少在 10 个细胞中表达
gene_counts <- Matrix::rowSums(pbmc_filtered@assays$RNA@counts > 0)
genes_to_keep <- names(gene_counts[gene_counts >= 10])

pbmc_filtered <- pbmc_filtered[genes_to_keep, ]

cat(sprintf("基因数: %d -> %d\\n", nrow(pbmc), nrow(pbmc_filtered)))
```

## 2.4 数据标准化

### 2.4.1 LogNormalize 方法

**原理：**
1. 将每个细胞的总表达量标准化到相同水平（默认 10,000）
2. 取自然对数（log(x+1)）

```r
# 标准标准化方法
pbmc_filtered <- NormalizeData(
    pbmc_filtered,
    normalization.method = "LogNormalize",
    scale.factor = 10000
)

# 查看标准化后的数据
head(pbmc_filtered@assays$RNA@data[1:5, 1:5])
```

**适用场景：**
- 数据质量较好
- 细胞间差异不大
- 快速初步分析

### 2.4.2 SCTransform 方法（推荐）

**原理：**
- 基于负二项分布的标准化方法
- 自动去除测序深度的影响
- 保留生物变异

```r
library(sctransform)

# SCTransform 标准化
pbmc_filtered <- SCTransform(
    pbmc_filtered,
    verbose = FALSE,
    ncells = 2000  # 如果细胞数很多，可以抽样
)

# SCTransform 会自动：
# 1. 标准化
# 2. 选择高变基因
# 3. 缩放数据
```

**优势：**
- 更稳健，不受极端值影响
- 自动处理技术噪声
- 适合大数据集

### 2.4.3 标准化效果评估

```r
# 比较标准化前后
par(mfrow = c(1, 2))

# 标准化前（使用原始 counts）
hist(
    pbmc@assays$RNA@counts["CD3D", ],
    breaks = 50,
    main = "Before Normalization",
    xlab = "CD3D Raw Counts",
    col = "lightblue"
)

# 标准化后
hist(
    pbmc_filtered@assays$RNA@data["CD3D", ],
    breaks = 50,
    main = "After Normalization",
    xlab = "CD3D Normalized Expression",
    col = "lightgreen"
)
```

## 2.5 高变基因选择

### 2.5.1 标准方法（vst）

```r
# 选择高变基因
pbmc_filtered <- FindVariableFeatures(
    pbmc_filtered,
    selection.method = "vst",  # 方差稳定变换
    nfeatures = 2000,          # 选择 top 2000 个高变基因
    mean.cutoff = c(0.1, 8),   # 平均表达量范围
    dispersion.cutoff = c(0.1, Inf)  # 离散度 cutoff
)

# 查看高变基因数量
cat(sprintf("高变基因数: %d\\n", length(VariableFeatures(pbmc_filtered))))

# 查看 top 20 高变基因
top20 <- head(VariableFeatures(pbmc_filtered), 20)
print(top20)
```

### 2.5.2 可视化高变基因

```r
# 绘制高变基因图
plot1 <- VariableFeaturePlot(pbmc_filtered)

# 标记 top 10 基因
top10 <- head(VariableFeatures(pbmc_filtered), 10)
plot2 <- LabelPoints(
    plot = plot1,
    points = top10,
    repel = TRUE,
    xnudge = 0,
    ynudge = 0
)

ggsave("variable_features.png", plot2, width = 10, height = 8, dpi = 300)
```

### 2.5.3 高变基因质量评估

```r
# 检查是否包含关键 marker 基因
markers <- c("CD3D", "CD3E", "CD14", "MS4A1", "NKG7", "GNLY", "FCGR3A")
overlap <- intersect(markers, VariableFeatures(pbmc_filtered))

cat(sprintf("Marker 基因覆盖: %d/%d\\n", length(overlap), length(markers)))
cat("覆盖的 marker:", paste(overlap, collapse = ", "), "\\n")

# 如果覆盖率低，可能需要调整参数或增加高变基因数量
```

## 2.6 本章小结

本章详细介绍了单细胞数据质量控制和标准化的完整流程：

**关键步骤：**
1. 计算 QC 指标（nCount, nFeature, percent.mt）
2. 可视化 QC 结果
3. 设定过滤阈值
4. 检测和去除双细胞
5. 过滤低表达基因
6. 数据标准化
7. 选择高变基因

**最佳实践：**
- 使用 MAD 方法设定阈值
- 必须检测双细胞
- 推荐使用 SCTransform
- 选择 2000 个高变基因

**常见问题：**
- 保留细胞比例过低：放宽阈值
- 高变基因覆盖差：增加 nfeatures
- 线粒体比例普遍偏高：检查样本质量''',
                'duration': 120
            }
        ]

        for i, lesson_data in enumerate(lessons_data):
            lesson = Lesson(
                toolkit_id=toolkit1.id,
                title=lesson_data['title'],
                content=lesson_data['content'],
                content_type='markdown',
                duration=lesson_data['duration'],
                sort_order=i,
                is_preview=(i == 0)
            )
            db.session.add(lesson)

        db.session.commit()
        print(f"✓ 创建了 10 个超详细工具包")
        print(f"✓ 工具包 1: {toolkit1.title}")
        print(f"  - 课程数: {len(lessons_data)}")
        print(f"  - 总时长: {sum(l['duration'] for l in lessons_data)} 分钟")

if __name__ == '__main__':
    create_comprehensive_toolkits()
