"""创建剩余 7 个工具包，覆盖所有文件格式"""
import os
import sys
sys.path.insert(0, '/root/workspace/zhihe/backend')
os.chdir('/root/workspace/zhihe/backend')

from dotenv import load_dotenv
load_dotenv('/etc/zhihe/backend.env')

from app import create_app
from models import db, User, Toolkit, Lesson

def create_remaining_toolkits():
    app = create_app()
    with app.app_context():
        author = User.query.first()

        toolkits_data = [
            {
                'title': '空间转录组学数据分析完全指南',
                'summary': '10x Visium + MERFISH 空间转录组分析：数据预处理、空间聚类、细胞互作、轨迹推断',
                'description': '空间转录组完整分析流程',
                'category': '空间组学',
                'level': '进阶',
                'lessons': [
                    ('空间转录组技术原理', 80, '# 空间转录组技术\n\n## 10x Visium\n- 分辨率: 55μm\n- 基于捕获探针\n- 适用于 FFPE 和冷冻样本\n\n## MERFISH\n- 单分子分辨率\n- 荧光原位杂交\n- 可检测数千个基因\n\n## Slide-seq\n- 10μm 分辨率\n- 基于 beads\n- 需要定制芯片'),
                    ('数据预处理', 90, '# 数据预处理\n\n## 加载数据\n```r\nlibrary(Seurat)\nlibrary(STutility)\n\nvisium <- Load10X_Spatial(\n  data.dir = "path/to/data/",\n  filename = "tissue_image.jpg",\n  assay = "Spatial"\n)\n```\n\n## 质控\n```r\nvisium <- SCTransform(visium, verbose = FALSE)\n```\n\n## 可视化\n```r\nSpatialPlot(visium, alpha = c(0.1, 1), features = c("Gene1", "Gene2"))\n```'),
                    ('空间聚类分析', 100, '# 空间聚类\n\n## Seurat 整合\n```r\n# 整合 scRNA-seq 数据\nanchors <- FindIntegrationAnchors(\n  object.list = list(sc_data, visium),\n  dims = 1:30\n)\nintegrated <- IntegrateData(anchorset = anchors)\n```\n\n## 空间域识别\n```r\nlibrary(SpaGCN)\nimport spagcn\n\n# 运行 SpaGCN\nspagcn.main(\n  input_dir="spatial_data",\n  out_dir="results"\n)\n```'),
                    ('细胞互作分析', 95, '# 细胞互作\n\n## CellChat\n```r\nlibrary(CellChat)\n\ncellchat <- createCellChat(\n  object = visium,\n  group.by = "cell_type"\n)\n\ncellchat <- identifyOverExpressedGenes(cellchat)\ncellchat <- computeCommunProb(cellchat)\ncellchat <- computeCommunProbPathway(cellchat)\n```\n\n## 可视化\n```r\nnetVisual_circle(cellchat@net$count, \n  vertex.weight = table(visium$cell_type),\n  title.name = "Cell-cell communication")\n```'),
                    ('差异表达与轨迹', 85, '# 差异与轨迹\n\n## 空间差异表达\n```r\nlibrary(SPARK)\n\nspark <- sparkx(\n  counts = visium@assays$Spatial@counts,\n  location = visium@images$slice@coordinates\n)\n```\n\n## 空间轨迹\n```r\nlibrary(SpaOT)\n\n# 计算最优传输\not_result <- compute_ot(\n  source = visium[, visium$timepoint == "T1"],\n  target = visium[, visium$timepoint == "T2"]\n)\n```'),
                ]
            },
            {
                'title': 'RNA-seq 差异表达分析完整教程',
                'summary': 'Bulk RNA-seq 标准流程：质控、比对、定量、差异分析、功能富集、可视化',
                'description': '转录组分析完整教程',
                'category': '转录组',
                'level': '入门',
                'lessons': [
                    ('数据质控与预处理', 85, '# 数据质控\n\n## FastQC\n```bash\nfastqc *.fastq.gz -o qc_reports/\n```\n\n## Trimmomatic\n```bash\ntrimmomatic PE \\\n  -threads 8 \\\n  input_R1.fastq input_R2.fastq \\\n  output_R1_paired.fastq output_R1_unpaired.fastq \\\n  output_R2_paired.fastq output_R2_unpaired.fastq \\\n  ILLUMINACLIP:adapters.fa:2:30:10 \\\n  LEADING:3 TRAILING:3 \\\n  SLIDINGWINDOW:4:15 \\\n  MINLEN:36\n```\n\n## MultiQC\n```bash\nmultiqc qc_reports/ -o multiqc_report/\n```'),
                    ('序列比对', 90, '# 序列比对\n\n## HISAT2\n```bash\n# 建立索引\nhisat2-build genome.fa genome_index\n\n# 比对\nhisat2 -x genome_index \\\n  -1 sample_R1.fastq -2 sample_R2.fastq \\\n  -S sample.sam \\\n  --dta \\\n  -p 16\n```\n\n## STAR\n```bash\n# 建立索引\nSTAR --runMode genomeGenerate \\\n  --genomeDir star_index/ \\\n  --genomeFastaFiles genome.fa \\\n  --sjdbGTFfile annotation.gtf\n\n# 比对\nSTAR --runThreadN 16 \\\n  --genomeDir star_index/ \\\n  --readFilesIn sample_R1.fastq sample_R2.fastq \\\n  --outFileNamePrefix sample_\n```'),
                    ('基因定量', 80, '# 基因定量\n\n## featureCounts\n```bash\nfeatureCounts -T 16 \\\n  -a annotation.gtf \\\n  -o counts.txt \\\n  *.bam\n```\n\n## Salmon (转录本水平)\n```bash\n# 建立索引\nsalmon index -t transcriptome.fa -i salmon_index\n\n# 定量\nsalmon quant -i salmon_index \\\n  -l A \\\n  -1 sample_R1.fastq -2 sample_R2.fastq \\\n  -p 16 \\\n  -o salmon_output/\n```'),
                    ('差异表达分析', 100, '# DESeq2 分析\n\n```r\nlibrary(DESeq2)\n\n# 读取数据\ncounts <- read.table("counts.txt", header = TRUE, row.names = 1)\ncoldata <- read.table("coldata.txt", header = TRUE, row.names = 1)\n\n# 创建 DESeq 对象\ndds <- DESeqDataSetFromMatrix(\n  countData = counts,\n  colData = coldata,\n  design = ~ condition\n)\n\n# 过滤低表达基因\nkeep <- rowSums(counts(dds) >= 10) >= 3\ndds <- dds[keep, ]\n\n# 差异分析\ndds <- DESeq(dds)\nres <- results(dds, contrast = c("condition", "treatment", "control"))\n\n# 结果汇总\nsummary(res)\n\n# 保存结果\nwrite.csv(as.data.frame(res), file = "differential_expression.csv")\n```'),
                    ('功能富集分析', 95, '# 功能富集\n\n## GO 富集\n```r\nlibrary(clusterProfiler)\nlibrary(org.Hs.eg.db)\n\n# 转换基因 ID\ngene_ids <- bitr(rownames(res)[which(res$padj < 0.05)], \n                 fromType = "SYMBOL", \n                 toType = "ENTREZID", \n                 OrgDb = org.Hs.eg.db)\n\n# GO 富集\ngo <- enrichGO(gene = gene_ids$ENTREZID,\n               OrgDb = org.Hs.eg.db,\n               ont = "BP",\n               pAdjustMethod = "BH",\n               pvalueCutoff = 0.05,\n               qvalueCutoff = 0.2)\n\n# 可视化\ndotplot(go, showCategory = 20)\n```\n\n## KEGG 富集\n```r\nkegg <- enrichKEGG(gene = gene_ids$ENTREZID,\n                   organism = "hsa",\n                   pvalueCutoff = 0.05)\n\nbarplot(kegg, showCategory = 20)\n```'),
                ]
            },
            {
                'title': '流式细胞术数据分析实战',
                'summary': 'FlowJo + R flowCore：门控策略、降维聚类、统计分析、可视化',
                'description': '流式数据分析完整教程',
                'category': '免疫学',
                'level': '进阶',
                'lessons': [
                    ('流式基础与数据格式', 75, '# 流式基础\n\n## FCS 文件格式\n- FCS 3.0/3.1 标准\n- 包含参数列表和事件数据\n- 支持多色荧光\n\n## 常用参数\n- FSC-A/H: 前向散射（细胞大小）\n- SSC-A/H: 侧向散射（复杂度）\n- FL1-H, FL2-H...: 荧光通道\n\n## 数据读取\n```r\nlibrary(flowCore)\n\n# 读取 FCS 文件\nflow_data <- read.FCS("sample.fcs")\n\n# 查看数据\nflow_data\n```\n\n## 数据转换\n```r\n# 对数转换\nflow_trans <- estimateLogicle(flow_data, channels = c("FL1-H", "FL2-H"))\nflow_data <- transform(flow_data, flow_trans)\n```'),
                    ('门控策略', 90, '# 门控策略\n\n## 基本门控\n```r\n# FSC/SSC 门控（去除碎片）\ngate1 <- RectangleGate(filterId = "cells",\n                       "FSC-A" = c(50000, 200000),\n                       "SSC-A" = c(10000, 150000))\ncells <- Subset(flow_data, gate1)\n\n# 单细胞门控（去除 doublets）\ngate2 <- PolygonGate(filterId = "singlets",\n                     "FSC-H" = c(20000, 150000),\n                     "FSC-A" = c(50000, 300000))\nsinglets <- Subset(cells, gate2)\n\n# 活细胞门控\n# 使用 viability dye\n```\n\n## 高级门控\n```r\n# 使用 flowClust\nlibrary(flowClust)\n\nclust <- flowClust(flow_data,\n                   K = 5,\n                   variableNames = c("FSC-A", "SSC-A"))\n\n# 可视化\nplot(clust, data = flow_data)\n```'),
                    ('自动化聚类', 95, '# 自动聚类\n\n## FlowSOM\n```r\nlibrary(FlowSOM)\n\n# 读取数据\nff <- ReadFlowFrames(list.files("data/", pattern = "\\.fcs$", full.names = TRUE))\n\n# 运行 FlowSOM\nflowSOM.res <- FlowSOM(ff,\n                       compensate = TRUE,\n                       transform = TRUE,\n                       scale = TRUE,\n                       colsToUse = c(9, 10, 11, 12, 13, 14),\n                       nClusters = 20)\n\n# 可视化\nPlotStars(flowSOM.res)\nPlotMarker(flowSOM.res, color = "CD3")\n```\n\n## UMAP 降维\n```r\nlibrary(Seurat)\n\n# 创建 Seurat 对象\nseurat_obj <- CreateSeuratObject(counts = flowSOM.res@cluster_codes)\n\n# UMAP\nseurat_obj <- RunUMAP(seurat_obj, dims = 1:20)\n\nDimPlot(seurat_obj, label = TRUE)\n```'),
                    ('统计分析与可视化', 85, '# 统计分析\n\n## 细胞频率比较\n```r\n# 提取细胞计数\ncounts <- table(flowSOM.res$metaClusters, flowSOM.res$sampleIDs)\n\n# 转换为频率\nfreq <- prop.table(counts, margin = 2)\n\n# 统计检验\nlibrary(ggpubr)\ncompare_means(freq ~ group, data = metadata, method = "t.test")\n```\n\n## 可视化\n```r\nlibrary(ggplot2)\n\n# 堆叠柱状图\nggplot(metadata, aes(x = sample, y = frequency, fill = cell_type)) +\n  geom_bar(stat = "identity", position = "fill") +\n  theme(axis.text.x = element_text(angle = 45, hjust = 1))\n\n# 热图\npheatmap::pheatmap(freq,\n                   cluster_rows = TRUE,\n                   cluster_cols = TRUE,\n                   show_rownames = TRUE)\n```'),
                    ('高级分析', 80, '# 高级分析\n\n## 差异丰度分析\n```r\nlibrary(diffcyt)\n\n# 准备数据\nd_input <- prepareData(flow_data, metadata, marker_name = "marker")\n\n# 差异分析\nd_res <- diffcyt(d_input,\n                 experiment_info = exp_info,\n                 contrast = c(1, -1))\n\n# 结果\ntopTable(d_res)\n```\n\n## 细胞亚群鉴定\n```r\n# 使用 SPADE\nlibrary(spade)\n\nspade_result <- run_spade(flow_data,\n                          markers = c("CD3", "CD4", "CD8"),\n                          n_clusters = 50)\n\n# 可视化\nplot_spade_tree(spade_result)\n```'),
                ]
            },
            {
                'title': '分子动力学模拟完全教程',
                'summary': 'GROMACS 蛋白质 MD 模拟：体系搭建、参数设置、模拟运行、结果分析',
                'description': '分子动力学实战教程',
                'category': '计算生物学',
                'level': '专业',
                'lessons': [
                    ('体系搭建', 100, '# 体系搭建\n\n## PDB 准备\n```bash\n# 下载 PDB\nwget https://files.rcsb.org/download/1ABC.pdb\n\n# 去除水和杂原子\ngrep -v "HOH" 1ABC.pdb > clean.pdb\n```\n\n## 拓扑生成\n```bash\n# 使用 pdb2gmx\ngmx pdb2gmx -f clean.pdb -o protein_GMX.itp -water tip3p\n\n# 选择力场\n# 1. AMBER99SB-ILDN\n# 2. CHARMM27\n# 3. OPLS-AA\n```\n\n## 盒子设置\n```bash\n# 设置盒子\ngmx editconf -c -d 1.0 -box 10 10 10\n\n# 添加溶剂\ngmx solvate -cp protein_box.gro -cs spc216.gro -o system_solvated.gro -p topol.top\n```\n\n## 添加离子\n```bash\n# 能量最小化（快速）\ngmx grompp -f ions.mdp -c system_solvated.gro -p topol.top -o ions.tpr\n\n# 添加离子\ngmx genion -s ions.tpr -o system_ions.gro -p topol.top -pname NA -nname CL -neutral\n```'),
                    ('能量最小化', 85, '# 能量最小化\n\n## 准备输入文件\n```bash\n# em.mdp\nintegrator = steep\nnsteps = 50000\nemtol = 100.0\nemstep = 0.001\n\ncutoff-scheme = Verlet\nnstlist = 10\nrcoulomb = 1.0\nrvdw = 1.0\n\nconstraints = none\n```\n\n## 运行能量最小化\n```bash\ngmx grompp -f em.mdp -c system_ions.gro -p topol.top -o em.tpr\ngmx mdrun -deffnm em\n```\n\n## 检查结果\n```bash\n# 查看势能\ngmx energy -f em.edr -o potential.xvg\n# 选择 "Potential"\n\n# 可视化\nxmgrace potential.xvg\n```'),
                    ('平衡模拟', 95, '# NVT 平衡\n\n```bash\n# nvt.mdp\nintegrator = md\nnsteps = 50000\ndt = 0.002\n\nnstxout-compressed = 500\nnstlog = 500\nnstenergy = 500\n\ncontinuation = no\nconstraint_algorithm = constraints\nconstraints = all-bonds\n\ncutoff-scheme = Verlet\nnstlist = 10\nrcoulomb = 1.0\nrvdw = 1.0\n\nTcoupl = V-rescale\ntau_t = 0.1\nref_t = 300\n\ngen_vel = yes\ngen_temp = 300\ngen_seed = -1\n```\n\n```bash\ngmx grompp -f nvt.mdp -c em.gro -r em.gro -p topol.top -o nvt.tpr\ngmx mdrun -deffnm nvt\n```\n\n# NPT 平衡\n\n```bash\n# npt.mdp (在 nvt.mdp 基础上添加)\nTcoupl = V-rescale\npcoupl = Parrinello-Rahman\ntau_p = 2.0\nref_p = 1.0\n```\n\n```bash\ngmx grompp -f npt.mdp -c nvt.gro -r nvt.gro -t nvt.cpt -p topol.top -o npt.tpr\ngmx mdrun -deffnm npt\n```'),
                    ('生产模拟', 90, '# 生产 MD\n\n```bash\n# md.mdp\nintegrator = md\nnsteps = 5000000  # 10 ns\ndt = 0.002\n\nnstxout-compressed = 5000  # 每 10 ps 保存一帧\nnstlog = 5000\nnstenergy = 5000\n\ncontinuation = yes\nconstraint_algorithm = constraints\nconstraints = all-bonds\n\ncutoff-scheme = Verlet\nnstlist = 20\nrcoulomb = 1.0\nrvdw = 1.0\n\nTcoupl = V-rescale\ntau_t = 0.1\nref_t = 300\n\npcoupl = Parrinello-Rahman\ntau_p = 2.0\nref_p = 1.0\n```\n\n```bash\ngmx grompp -f md.mdp -c npt.gro -t npt.cpt -p topol.top -o md.tpr\ngmx mdrun -deffnm md -nt 16 -nb gpu\n```\n\n# 监控运行\n```bash\ngmx energy -f md.edr -o temperature.xvg\n# 选择 Temperature, Pressure\n```'),
                    ('结果分析', 100, '# RMSD 分析\n```bash\ngmx rms -s md.tpr -f md.xtc -o rmsd.xvg -tu ns\n# 选择 Backbone\n```\n\n# RMSF 分析\n```bash\ngmx rmsf -s md.tpr -f md.xtc -o rmsf.xvg -res\n```\n\n# 回旋半径\n```bash\ngmx gyrate -s md.tpr -f md.xtc -o gyrate.xvg\n```\n\n# 氢键分析\n```bash\ngmx hbond -s md.tpr -f md.xtc -num hbonds.xvg\n```\n\n# 主成分分析\n```bash\ngmx covar -s md.tpr -f md.xtc -o eigenval.xvg -v eigenvec.trr\ngmx anaeig -s md.tpr -f md.xtc -v eigenvec.trr -first 1 -last 1 -projproj.xvg\n```\n\n# 可视化\n```bash\n# VMD\nvmd md.tpr md.xtc\n\n# PyMOL\npymol -c -d "load md.gro; load md.xtc; movie.produce md.mp4"\n```'),
                ]
            },
            {
                'title': '系统发育树构建完整指南',
                'summary': 'MEGA + RAxML：序列比对、模型选择、建树方法、Bootstrap 检验、可视化',
                'description': '进化分析完整教程',
                'category': '进化生物学',
                'level': '入门',
                'lessons': [
                    ('序列获取与比对', 85, '# 序列获取\n\n## 从 NCBI 下载\n```bash\n# 使用 Entrez Direct\nesearch -db nucleotide -query "COI[Gene] AND Homo sapiens[Organism]" | efetch -format fasta > sequences.fasta\n\n# 批量下载\ncat accessions.txt | while read acc; do\n  efetch -db nucleotide -id $acc -format fasta >> sequences.fasta\ndone\n```\n\n## 多序列比对\n```bash\n# MUSCLE\nmuscle -in sequences.fasta -out aligned.fasta\n\n# MAFFT\nmafft --auto sequences.fasta > aligned.fasta\n\n# ClustalW\nclustalw2 -infile=sequences.fasta -outfile=aligned.fasta\n```\n\n## 比对质量评估\n```bash\n# 使用 trimAl 去除低质量区域\ntrimal -in aligned.fasta -out trimmed.fasta -automated1\n```'),
                    ('进化模型选择', 80, '# 模型选择\n\n## jModelTest\n```bash\n# 下载 jModelTest\nwget https://github.com/ddarriba/jmodeltest2/releases/download/v2.1.10/jModelTest-2.1.10-linux.tar.gz\n\n# 运行\njava -jar jModelTest.jar -d trimmed.fasta -f -AIC -BIC -DT\n```\n\n## ModelFinder (IQ-TREE)\n```bash\niqtree -s trimmed.fasta -m MFP -nt AUTO\n```\n\n## 结果解读\n```\nBest-fit model: GTR+G+I\nAIC: 12345.67\nBIC: 12456.78\n```\n\n## 模型说明\n- GTR: 广义时间可逆模型\n- +G: Gamma 分布速率异质性\n- +I: 不变位点比例'),
                    ('最大似然法建树', 95, '# RAxML 建树\n\n```bash\n# 安装 RAxML\ngit clone https://github.com/stamatak/RAxML.git\ncd RAxML\nmake -f Makefile.PTHREADS.gcc\n\n# 运行 RAxML\nraxmlHPC-PTHREADS -T 16 \\\n  -m GTRGAMMA \\\n  -p 12345 \\\n  -x 12345 \\\n  -# 100 \\\n  -s trimmed.fasta \\\n  -n output_tree\n```\n\n## 参数说明\n- -m GTRGAMMA: 使用 GTR + Gamma 模型\n- -p 12345: 随机种子（用于快速启动）\n- -x 12345: Bootstrap 随机种子\n- -# 100: 100 次 Bootstrap 重复\n- -s: 输入比对文件\n- -n: 输出文件名\n\n## 输出文件\n- RAxML_bestTree.output_tree: 最佳树\n- RAxML_bipartitions.output_tree: 带 Bootstrap 值的树\n- RAxML_bootstrap.output_tree: Bootstrap 树'),
                    ('贝叶斯法建树', 90, '# MrBayes 建树\n\n```bash\n# 安装 MrBayes\nwget https://github.com/NBISweden/MrBayes/releases/download/v3.2.7a/mrbayes-3.2.7a.tar.gz\ntar -xzf mrbayes-3.2.7a.tar.gz\ncd mrbayes-3.2.7a\n./configure\nmake\nmake install\n```\n\n## 运行 MrBayes\n```bash\n# 准备 NEXUS 文件\n# trimmed.nex\n\n# 运行 MrBayes\nmb trimmed.nex\n\n# 在 MrBayes 中\n> lset nst=6 rates=gamma;\n> mcmc ngen=1000000 samplefreq=1000 printfreq=1000;\n> sump;\n> sumt;\n> quit;\n```\n\n## 参数说明\n- nst=6: GTR 模型\n- rates=gamma: Gamma 分布\n- ngen=1000000: 100 万代\n- samplefreq=1000: 每 1000 代采样一次\n\n## 结果\n- trimmed.nex.con: 一致性树\n- trimmed.nex.trp: 树文件\n- trimmed.nex.run1.p: 运行 1 参数\n- trimmed.nex.run2.p: 运行 2 参数'),
                    ('树的可视化与解读', 85, '# FigTree 可视化\n\n```bash\n# 下载 FigTree\nwget http://tree.bio.ed.ac.uk/software/figtree/\n\n# 打开树文件\nfigtree RAxML_bipartitions.output_tree\n```\n\n## iTOL 在线可视化\n```\n1. 访问 https://itol.embl.de/\n2. 上传树文件\n3. 添加注释（颜色、形状、标签）\n4. 导出图片\n```\n\n## 解读 Bootstrap 值\n- > 90%: 非常强的支持\n- 70-90%: 中等支持\n- 50-70%: 弱支持\n- < 50%: 不可靠\n\n## 树的编辑\n```r\n# 使用 R 的 ape 包\nlibrary(ape)\n\ntree <- read.tree("RAxML_bipartitions.output_tree")\n\n#  rooting\nrooted_tree <- root(tree, outgroup = "Species_A")\n\n# 绘图\nplot(rooted_tree, type = "phylogram")\n\n# 导出\nwrite.tree(rooted_tree, file = "rooted_tree.nwk")\n```'),
                ]
            },
            {
                'title': 'ChIP-seq 数据分析完整流程',
                'summary': '染色质免疫共沉淀测序：质控、比对、峰值鉴定、注释、motif 分析、差异结合',
                'description': '表观遗传学数据分析',
                'category': '表观遗传',
                'level': '进阶',
                'lessons': [
                    ('数据质控', 80, '# 数据质控\n\n## FastQC\n```bash\nfastqc *.fastq.gz -o qc_reports/\n```\n\n## 质控指标\n- 测序质量: Q30 > 80%\n- GC 含量: 正常范围\n- 接头污染: < 5%\n- 重复率: < 50%\n\n## 去接头\n```bash\n# Trim Galore\ntrim_galore --paired --quality 20 --stringency 5 \\\n  sample_R1.fastq.gz sample_R2.fastq.gz\n\n# 或使用 cutadapt\ncutadapt -a AGATCGGAAGAGC -A AGATCGGAAGAGC \\\n  -o sample_R1_trimmed.fastq.gz -p sample_R2_trimmed.fastq.gz \\\n  sample_R1.fastq.gz sample_R2.fastq.gz\n```'),
                    ('序列比对', 85, '# 比对到参考基因组\n\n## Bowtie2\n```bash\n# 建立索引\nbowtie2-build genome.fa genome_index\n\n# 比对\nbowtie2 -x genome_index \\\n  -1 sample_R1_trimmed.fastq.gz \\\n  -2 sample_R2_trimmed.fastq.gz \\\n  --very-sensitive \\\n  -p 16 \\\n  -S sample.sam\n```\n\n## BWA\n```bash\n# 建立索引\nbwa index genome.fa\n\n# 比对\nbwa mem -t 16 genome.fa \\\n  sample_R1_trimmed.fastq.gz \\\n  sample_R2_trimmed.fastq.gz > sample.sam\n```\n\n## 后处理\n```bash\n# 转换为 BAM\nsamtools view -bS sample.sam > sample.bam\n\n# 排序\nsamtools sort sample.bam -o sample_sorted.bam\n\n# 去重\nsamtools rmdup sample_sorted.bam sample_rmdup.bam\n\n# 索引\nsamtools index sample_rmdup.bam\n```'),
                    ('峰值鉴定', 100, '# MACS2 峰值鉴定\n\n```bash\n# 基本调用\nmacs2 callpeak \\\n  -t treatment.bam \\\n  -c control.bam \\\n  -n sample \\\n  -g hs \\\n  -q 0.05 \\\n  --outdir=macs2_output\n```\n\n## 参数说明\n- -t: 处理组 BAM\n- -c: 对照组 BAM（Input）\n- -n: 输出前缀\n- -g: 基因组大小（hs=human, mm=mouse）\n- -q: FDR 阈值\n- --outdir: 输出目录\n\n## 输出文件\n- sample_peaks.narrowPeak: 峰值文件\n- summits.bed: 峰值中心\n- treat_pileup.bdg: 信号轨迹\n- control_lambda.bdg: 背景轨迹\n\n## 评估结果\n```bash\n# 峰值数量\nwc -l sample_peaks.narrowPeak\n\n# 峰值分布\nawk \'{print $1, $2, $3, $7}\' sample_peaks.narrowPeak > peaks_summary.txt\n```'),
                    ('峰值注释', 90, '# 注释峰值\n\n## ChIPseeker (R)\n```r\nlibrary(ChIPseeker)\nlibrary(TxDb.Hsapiens.UCSC.hg38.knownGene)\n\n# 读取峰值\npeaks <- readPeakFile("sample_peaks.narrowPeak")\n\n# 注释到基因\npeakAnno <- annotatePeak(peaks, \n                         tssRegion = c(-3000, 3000),\n                         TxDb = TxDb.Hsapiens.UCSC.hg38.knownGene)\n\n# 可视化\nplotAnnoPie(peakAnno)\nplotDistToTSS(peakAnno)\n\n# 输出注释结果\nanno_df <- as.data.frame(peakAnno)\nwrite.csv(anno_df, "peak_annotation.csv")\n```\n\n## HOMER\n```bash\n# 注释\nannotatePeaks.pl sample_peaks.narrowPeak hg38 > annotation.txt\n\n# 生成报告\nmakeTagDirectory tagdir/ sample.bam\ngetPeakTaggedCount tagdir/ -hist 10 > peak_counts.txt\n```'),
                    ('Motif 分析与差异结合', 95, '# Motif 分析\n\n## HOMER findMotifsGenome.pl\n```bash\nfindMotifsGenome.pl sample_peaks.narrowPeak hg38 motif_output/ \\\n  -size 200 -mask\n```\n\n## MEME Suite\n```bash\n# 提取序列\nbedtools getfasta -fi genome.fa -bed sample_peaks.narrowPeak -fo peaks.fa\n\n# 运行 MEME\nmeme peaks.fa -dna -mod zoops -nmotifs 10 -minw 6 -maxw 20\n```\n\n# 差异结合分析\n\n## DiffBind\n```r\nlibrary(DiffBind)\n\n# 读取样本信息\nsamples <- dba.sampleSheet("samples.csv")\n\n# 创建 DBA 对象\ndba_obj <- dba(sampleSheet = samples)\n\n# 计数\ndba_obj <- dba.count(dba_obj)\n\n# 差异分析\ndba_obj <- dba.contrast(dba_obj, \n                        categories = DBA_TISSUE,\n                        minMembers = 2)\ndba_obj <- dba.analyze(dba_obj)\n\n# 结果\nres <- dba.report(dba_obj)\nwrite.csv(as.data.frame(res), "diffbind_results.csv")\n```'),
                ]
            },
            {
                'title': '冷冻电镜图像处理与三维重构',
                'summary': 'RELION + cryoSPARC：数据预处理、粒子挑选、二维分类、三维重构、模型精化',
                'description': '冷冻电镜单颗粒分析',
                'category': '结构生物学',
                'level': '专业',
                'lessons': [
                    ('数据预处理', 100, '# 运动校正\n\n## MotionCor2\n```bash\n# 安装\ngit clone https://github.com/ucsfbrc/motioncorr.git\ncd motioncorr\nmake\n\n# 运行\nmotioncorr -i Frames/ -o MotionCorr/ \\\n  -f 1 -gpu 0 -pat gain.mrc\n```\n\n## MotionCor2 (EMAN2)\n```bash\n# 使用 EMAN2 版本\ne2boxer.py --app motioncor2\n```\n\n## CTF 估计\n\n### CTFFIND4\n```bash\nctffind4 << EOF\nmotion_corrected_micrograph.mrc\nctf_output.mrc\n400\n0.007\n300\n2.0\n512\n5\n5\n30\n500\n5000\nno\nno\nEOF\n```\n\n### Gctf\n```bash\nGctf --kV 300 --cs 2.0 --apix 1.0 --ac 0.07 \\\n  --box 512 --res_start 30 --res_end 5 \\\n  micrograph.mrc\n```'),
                    ('粒子挑选', 95, '# 自动挑选\n\n## Gautomatch\n```bash\n# 运行\nGautomatch --apix 1.0 --diameter 200 \\\n  --lsigma 0.05 --hsigma 0.9 \\\n  micrograph.mrc\n\n# 输出\n# micrograph.autopick.box (坐标文件)\n```\n\n## crYOLO\n```bash\n# 训练模型\npython crYOLO.py --config config.yaml --train\n\n# 预测\npython crYOLO.py --config config.yaml --predict --input micrographs/\n```\n\n## Template-based picking\n```bash\n# RELION\nrelion_preprocess --i Particles/all_particles.star \\\n  --class2d_avg avg.mrc \\\n  --pick_refs avg.mrc \\\n  --pick micrographs/ \\\n  --pickname auto_pick\n```\n\n## 手动挑选\n```bash\n# EMAN2\ne2boxer.py micrographs/\n\n# 或 RELION\nrelion_display --i particles.star --gui\n```'),
                    ('二维分类', 90, '# 2D 分类\n\n## RELION\n```bash\n# 准备输入\nrelion_refine --o Class2D/run1 \\\n  --i Particles/auto_pick.star \\\n  --dont_combine_weights_via_disc \\\n  --parallel_disc_usage 4 \\\n  --ctf \\\n  --iter 25 \\\n  --tau2_fudge 2 \\\n  --particle_diameter 200 \\\n  --zero_mask \\\n  --oversampling 1 \\\n  --psi_step 6 \\\n  --offset_range 5 \\\n  --offset_step 2 \\\n  --norm \\\n  --scale \\\n  --j 8 \\\n  --gpu ""\n```\n\n## cryoSPARC\n```python\n# 使用 cryoSPARC Live 或 Ab-initio reconstruction\nimport cryosparc\n\ncs = cryosparc.Client()\n\n# 创建项目\nproject = cs.create_project("MyProject")\n\n# 导入粒子\njob = project.create_job("import_particles", ...)\njob.run()\n\n# 2D 分类\njob_2d = project.create_job("class_2D", \n    input=job.output,\n    num_classes=100)\njob_2d.run()\n```\n\n## 评估结果\n```bash\n# 查看分类\nrelion_display --i Class2D/run1_it025_data.star --gui\n\n# 选择好的类别\n# 标准：清晰的二级结构，无噪声\n```'),
                    ('初始模型', 85, '# 从头重构\n\n## RELION Ab-initio\n```bash\nrelion_refine --o Class3D/abinitio \\\n  --i Class2D/good_classes.star \\\n  --dont_combine_weights_via_disc \\\n  --ctf \\\n  --iter 25 \\\n  --tau2_fudge 2 \\\n  --particle_diameter 200 \\\n  --flatten_solvent \\\n  --zero_mask \\\n  --oversampling 1 \\\n  --healpix_order 2 \\\n  --offset_range 5 \\\n  --offset_step 2 \\\n  --sym C1 \\\n  --denovo_3dref \\\n  --j 8 \\\n  --gpu ""\n```\n\n## cryoSPARC Ab-initio\n```python\n# cryoSPARC\njob = project.create_job("homo_refine",\n    input=job_2d.output,\n    num_classes=4,\n    symmetry="C1")\njob.run()\n```\n\n## 评估初始模型\n```bash\n# 查看分辨率\ngrep "resolution" Class3D/abinitio/run_model.star\n\n# 可视化\nchimera Class3D/abinitio/run_class.mrc\n```\n\n## 选择最佳类别\n- 分辨率最高\n- 结构特征清晰\n- 粒子数充足'),
                    ('三维精化', 100, '# 高分辨率精化\n\n## RELION 3D auto-refine\n```bash\nrelion_refine --o Refine3D/run1 \\\n  --i Particles/good_particles.star \\\n  --ref Class3D/abinitio/run_class.mrc \\\n  --firstiter_cc \\\n  --ini_high 60 \\\n  --dont_combine_weights_via_disc \\\n  --ctf \\\n  --ctf_corrected_ref \\\n  --iter 25 \\\n  --tau2_fudge 4 \\\n  --particle_diameter 200 \\\n  --flatten_solvent \\\n  --zero_mask \\\n  --oversampling 1 \\\n  --healpix_order 3 \\\n  --offset_range 5 \\\n  --offset_step 2 \\\n  --sym C1 \\\n  --low_resol_join_halves 40 \\\n  --j 8 \\\n  --gpu ""\n```\n\n## 后处理\n```bash\nrelion_postprocess --i Refine3D/run1_half1_class.mrc \\\n  --i2 Refine3D/run1_half2_class.mrc \\\n  --o PostProcess/run1 \\\n  --angpix 1.0 \\\n  --mcf 1.2 \\\n  --autob_lowres 10\n```\n\n## 结果评估\n```bash\n# FSC 曲线\ngrep "fsc" PostProcess/run1_fsc.xml\n\n# 分辨率\ngrep "resolution" PostProcess/run1.star\n\n# 局部分辨率\nrelion_local_resolution --i PostProcess/run1.mrc \\\n  --o LocalResolution/run1 \\\n  --angpix 1.0\n\n# 可视化\nchimera PostProcess/run1.mrc\n```\n\n# 模型搭建\n\n## Phenix autoBuid\n```bash\nphenix.autobuild \\\n  map=PostProcess/run1.mrc \\\n  sequence=protein.fasta \\\n  resolution=3.5 \\\n  ncs=protein.ncs\n```\n\n## Coot 手动调整\n```bash\ncoot --map PostProcess/run1.mrc --pdb autobuild.pdb\n```'),
                ]
            },
        ]

        # 创建工具包
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

            # 创建课程
            for i, (title, duration, content) in enumerate(tk_data['lessons']):
                lesson = Lesson(
                    toolkit_id=toolkit.id,
                    title=title,
                    content=content,
                    content_type='markdown',
                    duration=duration,
                    sort_order=i,
                    is_preview=(i == 0)
                )
                db.session.add(lesson)

        db.session.commit()
        print(f"✓ 已创建 {len(toolkits_data)} 个工具包")
        for tk_data in toolkits_data:
            print(f"  - {tk_data['title']}")
            print(f"    课程数: {len(tk_data['lessons'])}")

if __name__ == '__main__':
    create_remaining_toolkits()
