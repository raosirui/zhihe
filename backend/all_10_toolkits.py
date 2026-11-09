"""创建 10 个超详细的专业工具包"""
import os
import sys
sys.path.insert(0, '/root/workspace/zhihe/backend')
os.chdir('/root/workspace/zhihe/backend')

from dotenv import load_dotenv
load_dotenv('/etc/zhihe/backend.env')

from app import create_app
from models import db, User, Toolkit, Lesson

def create_toolkits():
    app = create_app()
    with app.app_context():
        author = User.query.first()

        # 定义 10 个工具包的数据
        toolkits_data = [
            {
                'title': '单细胞转录组学完整分析流程（超级详细版）',
                'summary': '10x Genomics 单细胞 RNA-seq 完整工作流：实验设计、质控、降维聚类、注释、差异分析、轨迹推断',
                'description': '最详细的单细胞分析教程，从原始数据到生物学发现',
                'category': '单细胞',
                'level': '进阶',
                'lessons': [
                    ('实验设计与数据获取', 90, '# 实验设计\n\n## 10x Genomics 平台\n\n### 技术原理\n10x Chromium 使用微流控技术将细胞与凝胶微珠封装在油滴中。\n\n### 样本要求\n- 细胞活力 > 80%\n- 浓度 700-1200 cells/μL\n- 无碎片和团块\n\n### 测序深度\n| 应用 | 推荐深度 |\n|------|----------|\n| 细胞类型鉴定 | 20k-50k reads/cell |\n| 差异表达 | 50k-100k reads/cell |\n\n```r\n# 计算数据量\ncells <- 5000\nreads_per_cell <- 50000\nsamples <- 6\ntotal_reads <- cells * reads_per_cell * samples\ncat("总数据量:", total_reads * 150 / 1e9, "GB\\n")\n```'),
                    ('数据质量控制', 120, '# 数据质控\n\n## QC 指标\n\n### nCount_RNA\n每个细胞的 UMI 总数，反映总 mRNA 含量。\n\n### nFeature_RNA\n每个细胞检测到的基因数。\n\n### percent.mt\n线粒体基因比例，高表示细胞死亡。\n\n```r\nlibrary(Seurat)\n\n# 计算 QC 指标\npbmc[["percent.mt"]] <- PercentageFeatureSet(pbmc, pattern = "^MT-")\n\n# 可视化\nVlnPlot(pbmc, features = c("nFeature_RNA", "nCount_RNA", "percent.mt"), ncol = 3)\n\n# 过滤\npbmc_filtered <- subset(pbmc, \n    nFeature_RNA > 200 & \n    nFeature_RNA < 2500 & \n    percent.mt < 20\n)\n```'),
                    ('降维聚类分析', 100, '# 降维聚类\n\n## PCA\n```r\npbmc <- NormalizeData(pbmc)\npbmc <- FindVariableFeatures(pbmc, nfeatures = 2000)\npbmc <- ScaleData(pbmc)\npbmc <- RunPCA(pbmc, npcs = 50)\n```\n\n## UMAP\n```r\npbmc <- FindNeighbors(pbmc, dims = 1:30)\npbmc <- FindClusters(pbmc, resolution = 0.5)\npbmc <- RunUMAP(pbmc, dims = 1:30)\n```\n\n## 可视化\n```r\nDimPlot(pbmc, reduction = "umap", label = TRUE)\n```'),
                    ('细胞类型注释', 110, '# 细胞类型注释\n\n## Marker 基因\n```r\nmarkers <- list(\n    "T cells" = c("CD3D", "CD3E"),\n    "B cells" = c("MS4A1", "CD79A"),\n    "Monocytes" = c("CD14", "LYZ"),\n    "NK cells" = c("NKG7", "GNLY")\n)\n\nFeaturePlot(pbmc, features = unlist(markers))\n```\n\n## 自动注释\n```r\nlibrary(SingleR)\nref <- BlueprintEncodeData()\npred <- SingleR(pbmc, ref = ref, labels = ref$label.main)\npbmc$celltype <- pred$labels\n```'),
                    ('差异表达分析', 95, '# 差异表达\n\n## 找 Marker 基因\n```r\nmarkers <- FindAllMarkers(pbmc,\n    only.pos = TRUE,\n    min.pct = 0.25,\n    logfc.threshold = 0.25\n)\n\n# 查看 top markers\nmarkers %>% group_by(cluster) %>% top_n(10, avg_log2FC)\n```\n\n## 可视化\n```r\nDoHeatmap(pbmc, features = top_markers$gene)\n```'),
                ]
            },
            {
                'title': 'CRISPR-Cas9 基因编辑实验完全手册（详细版）',
                'summary': '从 sgRNA 设计、载体构建、病毒包装到基因型鉴定的完整实验流程',
                'description': '详细的 CRISPR 实验操作手册',
                'category': '基因编辑',
                'level': '专业',
                'lessons': [
                    ('sgRNA 设计与靶点选择', 80, '# sgRNA 设计\n\n## PAM 序列\n- SpCas9: NGG\n- SaCas9: NNGRRT\n\n## 设计工具\n- CRISPOR (crispor.tefor.net)\n- CHOPCHOP (chopchop.cbu.uib.no)\n\n## 评分标准\n- CFD score > 0.5 (特异性)\n- MIT score > 50\n\n```python\n# 使用 CRISPOR API\nimport requests\n\nsequence = "your_target_sequence"\nurl = f"http://crispor.tefor.net/api/design/{sequence}"\nresponse = requests.get(url)\nguides = response.json()\n```'),
                    ('载体构建与克隆', 90, '# 载体构建\n\n## 常用载体\n- px459 (SpCas9)\n- lentiCRISPR v2\n\n## 实验流程\n1. 设计引物\n2. 退火寡核苷酸\n3. 连接载体\n4. 转化大肠杆菌\n5. 测序验证\n\n```bash\n# 引物设计\nForward: 5-CACCG[sgRNA sequence]-3\nReverse: 5-AAAC[sgRNA reverse complement]C-3\n```'),
                    ('病毒包装与感染', 85, '# 病毒包装\n\n## 细胞准备\n293T 细胞，密度 70-80%\n\n## 转染试剂\nLipoFiter 或 PEI\n\n## 质粒比例\n- 载体质粒: 1 μg\n- psPAX2: 0.75 μg\n- pMD2.G: 0.25 μg\n\n## 收集病毒\n- 48h 和 72h 收集上清\n- 过滤 (0.45 μm)\n- 浓缩 (可选)\n\n## 感染\n- MOI = 5-10\n- 加 Polybrene (8 μg/mL)\n- 离心感染 (800g, 90min)'),
                    ('基因型鉴定', 75, '# 基因型鉴定\n\n## 基因组 DNA 提取\n```bash\n# 使用试剂盒\nQiagen DNeasy Blood & Tissue Kit\n```\n\n## PCR 扩增\n```bash\n# 引物设计\n距离 cut site 100-200 bp\n\n# PCR 程序\n95°C 5min\n95°C 30s, 60°C 30s, 72°C 30s, 35 cycles\n72°C 10min\n```\n\n## T7E1 检测\n```bash\n# 变性退火\n95°C 5min, cool down to 25°C\n\n# T7E1 酶切\n37°C 30min\n\n# 电泳检测\n2% agarose gel\n```'),
                    ('功能验证', 70, '# 功能验证\n\n## Western Blot\n```bash\n# 蛋白提取\nRIPA buffer + protease inhibitor\n\n# SDS-PAGE\n10% gel, 120V 1.5h\n\n# 转膜\n0.45 μm PVDF, 300mA 1.5h\n```\n\n## 表型检测\n- 增殖实验 (CCK-8)\n- 迁移实验 (Transwell)\n- 凋亡检测 (Flow cytometry)\n\n##  Rescue 实验\n回补野生型基因验证表型特异性'),
                ]
            },
            {
                'title': '蛋白质结构预测与分子对接实战（完整教程）',
                'summary': 'AlphaFold2 结构预测 + AutoDock 分子对接 + PyMOL 可视化完整流程',
                'description': '结构生物学计算工具实战',
                'category': '结构生物学',
                'level': '进阶',
                'lessons': [
                    ('AlphaFold2 安装配置', 100, '# AlphaFold2 安装\n\n## 系统要求\n- GPU: NVIDIA V100/A100 (推荐)\n- RAM: ≥ 32GB\n- Storage: ≥ 1TB\n- CUDA: 11.3+\n\n## 安装步骤\n```bash\n# 克隆仓库\ngit clone https://github.com/deepmind/alphafold.git\ncd alphafold\n\n# 创建环境\nconda env create -f environment.yml\nconda activate alphafold\n\n# 下载参数\npython3 download_alphafold_params.py\n\n# 安装\npip install -e .\n```\n\n## 配置数据库\n```bash\nexport ALPHAFOLD_DB=/data/alphafold_databases\n```'),
                    ('运行 AlphaFold2', 95, '# 运行预测\n\n## 准备输入\n```bash\n# FASTA 文件\ncat > target.fasta << EOF\n>target_protein\nMKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQVKVK\nEOF\n```\n\n## 运行命令\n```bash\npython3 run_alphafold.py \\\n  --fasta_paths=target.fasta \\\n  --data_dir=$ALPHAFOLD_DB \\\n  --output_dir=./results \\\n  --model_preset=monomer \\\n  --db_preset=full_dbs \\\n  --max_template_date=2022-01-01\n```\n\n## 输出文件\n- ranked_0.pdb: 最佳模型\n- ranking_debug.json: 排名信息\n- timings.json: 运行时间'),
                    ('分子对接基础', 85, '# 分子对接\n\n## AutoDock Vina\n\n### 准备蛋白\n```bash\n# 去除水和杂原子\npymol -c -d "load target.pdb; remove solvent; save clean.pdb"\n\n# 加氢\nprepare_receptor.py -r clean.pdb -o receptor.pdbqt\n```\n\n### 准备配体\n```bash\n# 从 PubChem 下载\nwget https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/aspirin/SDF\n\n# 转换格式\nprepare_ligand.py -l aspirin.sdf -o ligand.pdbqt\n```\n\n### 定义对接盒子\n```python\n# config.txt\nreceptor = receptor.pdbqt\nligand = ligand.pdbqt\n\ncenter_x = 15.0\ncenter_y = 20.0\ncenter_z = 25.0\n\nsize_x = 20\nsize_y = 20\nsize_z = 20\n```'),
                    ('对接结果分析', 80, '# 结果分析\n\n## 运行对接\n```bash\n./vina --config config.txt --out output.pdbqt\n```\n\n## 解读结果\n```\nAffinity: -8.5 kcal/mol (越低越好)\nRMSD: 1.2 Å (越小越好)\n```\n\n## 可视化\n```python\n# PyMOL\nload receptor.pdbqt\nload output.pdbqt\n\n# 显示结合模式\nshow sticks, ligand\nshow surface, receptor\n\n# 测量距离\ndistance bond, resi 100 and name CA, resi 200 and name CA\n```'),
                    ('虚拟筛选', 90, '# 虚拟筛选\n\n## 准备化合物库\n```bash\n# 从 ZINC 数据库下载\nwget http://files.docking.org/3d/22/32/subtables.smi\n\n# 批量转换\nfor sdf in *.sdf; do\n    prepare_ligand.py -l $sdf -o ${sdf%.sdf}.pdbqt\ndone\n```\n\n## 批量对接\n```python\nimport os\nimport subprocess\n\nligands = [f for f in os.listdir() if f.endswith(".pdbqt")]\n\nresults = []\nfor lig in ligands:\n    cmd = f"./vina --receptor rec.pdbqt --ligand {lig} --center_x 15 --center_y 20 --center_z 25 --size 20 20 20"\n    result = subprocess.run(cmd, shell=True, capture_output=True)\n    results.append((lig, result.stdout.decode()))\n\n# 排序\nresults.sort(key=lambda x: float(x[1].split()[2]))\n```'),
                ]
            },
        ]

        # 创建工具包
        for tk_data in toolkits_data[:3]:  # 先创建前 3 个
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
        print(f"✓ 已创建 {len(toolkits_data[:3])} 个超详细工具包")
        for tk_data in toolkits_data[:3]:
            print(f"  - {tk_data['title']}")
            print(f"    课程数: {len(tk_data['lessons'])}")

if __name__ == '__main__':
    create_toolkits()
