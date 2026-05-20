# 小麦 GoldenBraid2.0表达载体构建及遗传转化完整实验流程 SOP

**物种：** Triticum aestivum（普通小麦，六倍体 AABBDD）  
**系统：** GoldenBraid2.0  
**转化策略：** 幼胚农杆菌介导稳定转化（Agrobacterium-mediated immature embryo transformation）  
**验证策略：** 稳定遗传转化（PCR鉴定+qPCR定量）→ T1代三亚基因组纯合筛选  
**文件版本：** v1.0 | 日期：2026-04-06

## 总体流程概览
IWGSC RefSeq v2.1 基因组数据库
│
▼
Step 1: 获取目的基因序列（CDS编码区序列）
│
▼
Step 2: 表达载体构建（单基因推荐pCAMBIA系列载体，多基因推荐GoldenBraid2.0组装系统）
│
▼
Step 3: 小麦稳定遗传转化（A. tumefaciens AGL1，幼胚法）
│
▼
Step 4: T0阳性植株叶片基因组提取（PCR鉴定）
│
▼
Step 5: T0自交 → T1代筛选（qPCR定量）→ 获得稳定遗传过表达株系

## Step 1：获取目的基因序列
### 1.1 目标
从IWGSC数据库获取获取目的基因的完整CDS编码区序列，用于后续基因表达载体构建及稳定遗传转化。

### 1.2 所需信息
- IWGSC基因ID（如：TraesCS1A02G000100）或NCBI登录号
- 三个亚基因组的同源基因ID（A、B、D染色体组）

### 1.3 操作步骤
**（A）获取基因序列**
1.  访问 IWGSC EnsemblPlants：[https://plants.ensembl.org/Triticum_aestivum/](https://plants.ensembl.org/Triticum_aestivum/"%20t%20"_blank)
2.  搜索目的基因，获取三个亚基因组的同源基因（homeologs）的CDS编码区序列（FASTA格式）
>_gene_name___gene_accession_
_gene_sequence_
>_gene_name___gene_accession_up2k
_gene_sequence_up2k_
>_gene_name___gene_accession_down2k
_gene_sequence_down2k_

**（B）多倍体同源基因分析**
1.  使用 BLAST 在 IWGSC RefSeq v2.1 中比对，确认三个亚基因组的同源基因
2.  多序列比对（MAFFT/ClustalW）分析三亚基因组序列保守性

**（C）参考资源**
|     |     |     |
| --- | --- | --- |
| **数据库** | **网址** | **用途** |
| IWGSC EnsemblPlants | https://plants.ensembl.org/Triticum_aestivum/ | 基因序列、注释 |
| URGI WheatIS | https://wheat-urgi.versailles.inrae.fr/ | 小麦基因组数据 |
| NCBI | https://www.ncbi.nlm.nih.gov/ | 序列下载 |
| SoyBase（参考） | https://www.soybase.org | —   |

## Step 2：单基因表达载体构建（常用于单基因表达验证）
2.1 载体选择：推荐pCAMBIA系列表达载体，CaMV35启动子，为植物常用表达载体。
2.2 标签选择：根据实验需求添加eGFP、GUS、3xflag等。
2.3 载体构建流程：
（1） 基因克隆及PCR产物纯化，参考胶回收纯化试剂盒说明书进行；
（2）pCAMBIA系列载体单酶切线性化，参考单酶切说明书进行；
（3）一步克隆法构建重组表达载体，参考一步克隆试剂盒说明书进行；
（4） 转化_E.coli_感受态细胞；
（5）单克隆鉴定及测序；
（6）质粒提取，用于农杆菌感受态细胞转化。

## Step 2：GoldenBraid2.0多基因表达载体构建（常用于代谢工程研究）
### 2.1 GoldenBraid2.0表达载体选择
|     |     |     |
| --- | --- | --- |
| **载体** | **特点** | **来源** |
| pUPD2 | Donor载体，氯霉素抗性，用于启动子子、结构基因、终止子等元件供体骨架，含有 LacZ 基因，可通过蓝白斑筛选。 | Addgene #68161 |
| pDGB_α1 | 植物双元表达载体，卡那霉素抗性，pUPD2元件可组装为任意一个α**_**level表达单元；或两个不同Ω**_**level表达载体转换成任意一个α**_**level表达载体。含有 LacZ 基因，可通过蓝白斑筛选。 | Addgene #68228 |
| pDGB_α2 | 植物双元表达载体，卡那霉素抗性，pUPD2元件可组装为任意α**_**level表达单元；或两个不同Ω**_**level表达载体转换成任意一个α**_**level表达载体。含有 LacZ 基因，可通过蓝白斑筛选。 | Addgene #68229 |
| pDGB_Ω1 | 植物双元表达载体，壮观霉素抗性，可通过两个不同α**_**level表达载体转换成任意一个Ω**_**level表达载体。含有 LacZ 基因，可通过蓝白斑筛选。 | Addgene #68238 |
| pDGB_Ω2 | 植物双元表达载体，壮观霉素抗性，可通过两个不同α**_**level表达载体转换成任意一个Ω**_**level表达载体。含有 LacZ 基因，可通过蓝白斑筛选。 | Addgene #68239 |

### 2.2 GoldenBraid2.0多基因载体组装流程
#### 2.2.1 表达元件克隆与pUPD2入门载体构建
启动子选择及pUPD2入门载体构建
根据自身实验需求，若需要目标基因在全生育期表达，推荐CaMV 35S、SlEF1a、AtOcs、AtUbi、AtActin等组成型启动子。

（1）启动子构建pUPD2入门载体oligo设计：
正向oligo：GCGCCGTCTCGCTCGGGAG + [22 ~25 nt 启动子上游序列]
反向oligo：GCGCCGTCTCGCTCACATT + [22 ~25 nt 启动子下游反向互补序列]

（2）基因克隆及pUPD2入门载体构建
目标基因构建pUPD2入门载体oligo设计：
_gene_name_
正向oligo：GCGCCGTCTCGCTCGAATG + [_gene_up25_]（22 ~25 nt 目标基因上游序列，去掉ATG）
反向oligo：GCGCCGTCTCGCTCAAAGC+ [_gene_down25_rc_]（22 ~25 nt 目标基因下游反向互补序列）

（3）终止子选择及pUPD2入门载体构建
根据自身实验需求，可选择T35S、Tnos、TActin、THsp18.2、TUbq3等常用终止子。
终止子构建pUPD2入门载体oligo设计：
正向oligo：GCGCCGTCTCGCTCGGCTT + [22 ~25 nt 终止子上游序列]
反向oligo：GCGCCGTCTCGCTCAAGCG + [22 ~25 nt 终止子下游反向互补序列]

表达元件构建pUPD2入门载体边切边连体系：
|     |     |
| --- | --- |
| Component | Volume |
| pUPD2 | 75 ng |
| Each of GB part | 70~90 ng |
| _BsmBI_（Thermo） | 0.5 μL |
| Buffer Tango | 1 μL |
| T4 DNA ligase (NEB) | 0.5 μL |
| T4 DNA ligase buffer | 1 μL |
| Total（H<sub>2</sub>O补足） | 10 μL |

表达元件构建pUPD2入门载体边切边连程序：
• 37°C × 20 min
• 37°C × 3 min
30~35 cycles
• 16°C × 4 min
• 50°C × 5 min
• 80°C × 5 min
• 4°C 保存

（4）_E.coli_感受态细胞转化
将上述连接产物10 μL 转化至DH5α感受态细胞（ 氯霉素抗性），进行蓝白斑筛选，挑取白色菌落进行 PCR 或测序鉴定。
测序/PCR鉴定通用引物（产物大小约 180 bp + GB Part）：
pUPD2-F： GCTTTCGCTAAGGATGATTTCTGG
pUPD2-R：GAAGCCTGCATAACGCGAAGTAATC

#### 2.2.2 pDGB_α1/pDGB_α2表达单元组装
将构建好的pUPD2-part载体与pDGB_α1/ pDGB_α2载体同时进行酶切-连接反应，通过蓝白斑筛选，获得含有完整表达单元的pDGB_α1或pDGB_α2双元植物表达载体。
1）pDGB_α1/ pDGB_α2表达单元组装酶切-连接体系：
|     |     |
| --- | --- |
| Component | Volume |
| pDGB_α1 | 75 ng |
| Each of pUPD2-GB Parts | 70~90 ng |
| BsaI（NEB） | 0.5 μL |
| Buffer CutSmart | 1 μL |
| T4 DNA ligase (NEB) | 0.5 μL |
| T4 DNA ligase buffer | 1 μL |
| Total（H<sub>2</sub>O补足） | 10 μL |

（2）pDGB_α1/ pDGB_α2表达单元组装酶切-连接程序：同3.2.1（3）

（3）将上述连接产物10 μL 转化至DH5α感受态细胞（卡那霉素抗性），进行蓝白斑筛选，挑取白色菌落进行 PCR鉴定。

#### 2.2.3 pDGB3_α和pDGB3_Ω载体互换（多个表达单元组装）
（1）两个pDGB3_α载体(pDGB3_α1 + pDGB3_α2)可组装成任意一个pDGB3_Ω1/ pDGB3_Ω2载体，两个pDGB3_Ω载体(pDGB3_Ω1 + pDGB3_Ω2)组装成任意一个pDGB3_α1/pDGB3_α2载体。

（2）pDGB3_α (pDGB3_α1 + pDGB3_α2)载体转换为pDGB3_Ω载体酶切-连接体系：
|     |     |
| --- | --- |
| Component | Volume |
| pDGB3_α1 (TU1) | 75 ng |
| pDGB3_α2 (TU2) | 75 ng |
| pDGB3_Ω1 | 75 ng |
| _BsmBI_（Thermo） | 1 μL |
| Buffer Tango | 2 μL |
| T4 DNA ligase (NEB) | 1 μL |
| T4 DNA ligase buffer | 2 μL |
| Total | 20 μL |

（3）pDGB3_Ω (pDGB3_Ω1 + pDGB3_Ω2)载体转换为pDGB3_α载体酶切-连接体系：
|     |     |
| --- | --- |
| Component | Volume |
| pDGB3_Ω1 (TU1 + TU2) | 75 ng |
| pDGB3_Ω2 (TU3) | 75 ng |
| pDGB3_α1 | 75 ng |
| _BsaI_（NEB） | 1 μL |
| Buffer CutSmart | 2 μL |
| T4 DNA ligase (NEB) | 1 μL |
| T4 DNA ligase buffer | 2 μL |
| Total | 20 μL |

（4）pDGB3_α和pDGB3_Ω载体互换酶切-连接程序：同3.2.1（3）

（5）将上述连接产物转化至DH5α感受态细胞，进行蓝白斑筛选，挑取白色菌落进行 PCR鉴定。

#### 2.2.4 蓝白斑筛选方法
（1）X-gal（20 mg/ml）用二甲基亚砜（DMSO）或二甲基甲酰胺（DMF）溶解，-20 ℃避光保存；
（2）IPTG（ 24mg/ml, 100 mM），用ddH2O溶解，过滤除菌，-20 ℃保存；
（3）在100 mL的LB固体培养基中，分别加入200 μL X-gal，100 μL IPTG，和相应浓度抗生素，制成X-gal、IPTG、抗生素平板培养基备用。
**注：pUPD2入门载体为氯霉素抗性，pDGB3_α载体为卡那霉素抗性，pDGB3_Ω载体为壮观霉素抗性。所有连接载体通过蓝白斑筛选，挑取白色克隆进行菌落鉴定。**

## Step 3：小麦稳定遗传转化（幼胚农杆菌介导法）
### 3.1 原理
利用根癌农杆菌（A. tumefaciens AGL1）介导的幼胚转化法，是小麦稳定转化的主流方法，转化效率约5-15%。幼胚（授粉后12-14天）具有高度分裂活性，是小麦转化的最佳外植体。

### 3.2 材料准备
- **菌株：** A. tumefaciens AGL1（或EHA105）（含过表达载体）
- **小麦品种：** Fielder、Bobwhite、Kenong199（转化效率较高）
- **幼胚获取：** 授粉后12-14天，幼胚直径1.5-2 mm
- **种植条件：** 温室，16 h光照/8 h黑暗，20-22°C

### 3.3 培养基配方
|     |     |     |
| --- | --- | --- |
| **培养基** | **成分** | **用途** |
| 预培养基（Pre-CM） | MS + 2 mg/L 2,4-D + 3%蔗糖 + 0.8%琼脂，pH 5.8 | 幼胚预培养 |
| 侵染培养基（IM） | MS液体 + 200 μM AS，pH 5.4 | 农杆菌侵染 |
| 共培养基（CCM） | MS + 2 mg/L 2,4-D + 200 μM AS + 0.8%琼脂，pH 5.4 | 共培养 |
| 愈伤诱导/筛选培养基（CIM） | MS + 2 mg/L 2,4-D + 500 mg/L Cefotaxime + 5 mg/L Bialaphos + 0.8%琼脂，pH 5.8 | 愈伤诱导与筛选 |
| 分化培养基（DIM） | MS + 0.5 mg/L BAP + 0.1 mg/L NAA + 500 mg/L Cefotaxime + 5 mg/L Bialaphos + 0.8%琼脂，pH 5.8 | 芽分化 |
| 生根培养基（RM） | 1/2 MS + 1 mg/L IBA + 0.8%琼脂，pH 5.8 | 生根  |

**筛选标记：** bar基因（草丁膦/Bialaphos抗性）为小麦转化首选；也可使用Hygromycin（25 mg/L）。

### 3.4 农杆菌制备
1.  将含过表达载体的AGL1划线至含抗生素的YEB平板：
    - 利福平（Rifampicin）：25 μg/mL
    - 载体抗生素（如卡那霉素：50 μg/mL）
2.  28°C培养2天
3.  挑单菌落接种至5 mL YEB液体培养基，28°C过夜
4.  转接至50 mL YEB，培养至OD₆₀₀ = 0.6-0.8（约4-6 h）
5.  4000 rpm离心10 min，弃上清
6.  用侵染培养基（MS液体 + 200 μM AS）重悬至OD₆₀₀ = 0.5-0.6
7.  室温静置30 min（活化vir基因）

### 3.5 幼胚获取与预培养
**Day -2 至 Day 0 — 幼胚获取：**
1.  选取授粉后12-14天的小麦穗（幼胚直径1.5-2 mm）
2.  表面消毒：
    - 70%乙醇：1 min
    - 10% NaClO（含0.1% Tween-20）：10 min
    - 无菌水洗涤5次
3.  在超净工作台中剥取幼胚（盾片朝上）
4.  将幼胚转移至Pre-CM培养基，25°C预培养4-6 h（或过夜）

### 3.6 农杆菌侵染与共培养
**Day 0 — 侵染：**
1.  将预培养的幼胚转移至侵染培养基（含菌液，OD₆₀₀ = 0.5-0.6）
2.  室温侵染5-10 min，轻柔摇动
3.  用无菌滤纸吸干多余菌液
4.  将幼胚（盾片朝上）放置于CCM培养基
5.  25°C，黑暗，共培养3天

### 3.7 愈伤诱导与筛选
**Day 3 — 转移至筛选培养基：**
1.  用含500 mg/L Cefotaxime的无菌水洗涤幼胚3次（去除农杆菌）
2.  用无菌滤纸吸干，转移至CIM培养基
3.  25°C，16 h光照/8 h黑暗培养
4.  每2周继代一次（转移至新鲜CIM培养基）
5.  培养4-6周，观察愈伤组织生长：
    - 阳性愈伤：在Bialaphos压力下正常生长，呈淡黄色/白色
    - 阴性愈伤：生长受抑，褐化死亡

### 3.8 芽分化与伸长
**Day 42-56 — 分化：**
1.  将筛选阳性愈伤（直径约5-10 mm）转移至DIM培养基
2.  25°C，16 h光照/8 h黑暗培养
3.  每2周继代一次
4.  约3-4周后可见绿色芽点出现
5.  当芽长至1-2 cm时，切下转移至新鲜DIM培养基继续伸长

### 3.9 生根与移栽
**生根诱导（约2-3周）：**
1.  将伸长的芽（3-5 cm）切下，基部切口斜切
2.  转移至RM培养基（含IBA 1 mg/L）
3.  25°C，16 h光照/8 h黑暗培养
4.  约2-3周后可见白色根系生长

**炼苗与移栽：**
1.  打开培养瓶盖，室温炼苗2-3天
2.  用温水轻柔洗去根部琼脂
3.  移栽至蛭石:珍珠岩:营养土（1:1:2）混合基质
4.  套袋保湿，22°C，16 h光照培养
5.  每天喷水保湿，逐渐减少保湿频率
6.  2周后去袋，正常管理
7.  移栽成活后，取叶片进行PCR鉴定（T-DNA整合）

**转化效率参考：** Fielder品种幼胚法转化效率约5-15%，每次实验建议处理100-200个幼胚。

## Step 4：T0阳性植株鉴定与扩增子测序
### 4.1 T0植株初步鉴定（PCR法）
**目的：** 确认T-DNA整合，排除假阳性
**引物设计：**
- 正向引物：位于35S启动子（或根据自身选用启动子设计）
- 反向引物：位于目的基因外显子区域

### 4.2 叶片基因组DNA提取（PCR鉴定）
**材料：** 约100 mg新鲜叶片（或冻干叶片粉末）
**CTAB法提取：**
- 液氮研磨叶片至细粉
- 加入700 μL 2× CTAB缓冲液（65°C预热）
- 65°C水浴30 min，每10 min轻柔混匀
- 加入等体积氯仿:异戊醇（24:1），充分混匀
- 12000 rpm离心10 min，取上清
- 重复氯仿抽提一次
- 加入2/3体积冷异丙醇，-20°C沉淀30 min
- 12000 rpm离心10 min，弃上清
- 70%乙醇洗涤沉淀两次
- 室温晾干，溶于50 μL TE缓冲液
- NanoDrop定量：OD₂₆₀/₂₈₀ = 1.8-2.0

以上述基因组DNA为模板，通过PCR扩增对T0转基因植物进行阳性材料鉴定。

**PCR扩增条件**（25 μL体系）**：**
|     |     |
| --- | --- |
| **组分** | **用量** |
| 2× Taq PCR Master Mix | 12.5 μL |
| 正向引物（10 μM） | 1 μL |
| 反向引物（10 μM） | 1 μL |
| 基因组DNA（50-100 ng） | 2 μL |
| ddH₂O | 8.5 μL |

PCR程序：
- 95°C 3 min
- 35个循环：95°C 30 sec / 60°C 30 sec / 72°C 1 min
- 72°C 5 min
- 4°C 保存

将PCR产物进行琼脂糖凝胶电泳检测。
预期条带：仅在转基因植株中出现

## Step 5：T1代筛选——qPCR定量确定目标基因表达水平
### 5.1 T0植株自交
- 选择T0代PCR鉴定阳性植株
- 收获T1代种子（每株收取30-50粒）
- 种子干燥后4°C保存

### 5.2 T1代种植与qPCR基因表达分析
**T1代种植：**
- 每个T0株系种植20-30株T1代植株
- 正常温室或田间水培管理

**qPCR基因表达水平检测：**
（A）提取T1代阳性植株Total RNA，并反转录成第一链cDNA。
（B）设计目标基因qPCR定量引物，长度约90~200 bp。
（C）以cDNA为模板，对目标基因进行qPCR定量分析，以小麦TaActin或TaGAPDH基因为内参。
（D）挑选满足实验需求的不同表达水平的株系进行T2代表型鉴定。
（E）表型观察：
- 记录与野生型相比的表型差异
- 农艺性状评估（株高、分蘖数、穗型、粒重等）

### 5.3 株系命名与保存
**命名规则：**[T0株系号]-[T1株系号]- [启动子]-[基因名]
例： T0-3-T1-7-P35S-Taxxx

**保存：**
- 收取T1纯合编辑植株种子（T2代种子）
- 每株系保存至少50粒种子，-20°C干燥保存
- 建立株系档案（基因型、表型、测序结果）