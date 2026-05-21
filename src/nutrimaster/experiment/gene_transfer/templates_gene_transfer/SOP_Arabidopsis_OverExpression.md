# 拟南芥GoldenBraid2.0表达载体构建及遗传转化完整实验流程 SOP

**物种：** Arabidopsis thaliana（拟南芥，二倍体，2n=10）  
**系统：** GoldenBraid2.0  
**转化策略：** 花序浸染法（Floral dip transformation，农杆菌介导）  
**验证策略：** 稳定遗传转化（PCR鉴定+qPCR定量）→ T2代稳定遗传筛选  
**文件版本：** v1.0 | 日期：2026-04-06

## 总体流程概览
TAIR / Ensembl Plants 拟南芥基因组数据库
│
▼
Step 1: 获取目的基因序列（CDS编码区序列）
│
▼
Step 2: 表达载体构建（单基因推荐pCAMBIA系列载体，多基因推荐GoldenBraid2.0组装系统）
│
▼
Step 3: 拟南芥稳定遗传转化（A. tumefaciens GV3101，花序浸染法）
│
▼
Step 4: T1代种子筛选（抗性筛选）
│
▼
Step 5: T1自交 → T2代筛选 → （PCR鉴定+qPCR定量）→ 获得稳定遗传基因过量表达株系

**注意：** 拟南芥不适用毛状根快速验证系统（此处指稳定转化流程）。花序浸染法是拟南芥稳定转化的标准方法，操作极为简便，无需组织培养，转化效率约0.1-1%（T1代种子中阳性率）。

## Step 1：获取目的基因序列
### 1.1 目标
从TAIR数据库获取目的基因CDS序列，用于后续基因克隆及植物表达载体构建。

### 1.2 所需信息
- TAIR基因ID（如：AT1G01010）或NCBI登录号
- 拟南芥参考基因组版本：TAIR10（Col-0生态型）

### 1.3 操作步骤
**（A）获取基因序列**
1.  访问 TAIR（The Arabidopsis Information Resource）：[https://www.arabidopsis.org/](https://www.arabidopsis.org/"%20t%20"_blank)
2.  搜索目的基因，下载CDS编码区及上下游序列
>_gene_name___gene_accession_
_gene_sequence_
>_gene_name___gene_accession_up2k
_gene_sequence_up2k_
>_gene_name___gene_accession_down2k
_gene_sequence_down2k_

**参考资源**
|     |     |     |
| --- | --- | --- |
| **数据库** | **网址** | **用途** |
| TAIR | https://www.arabidopsis.org/ | 拟南芥基因序列、注释、T-DNA插入系 |
| Ensembl Plants | https://plants.ensembl.org/Arabidopsis_thaliana/ | 基因组注释 |
| NCBI | https://www.ncbi.nlm.nih.gov/ | 序列下载 |
| ABRC | https://abrc.osu.edu/ | 种子资源 |

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
（1）pDGB_α1/ pDGB_α2表达单元组装酶切-连接体系：
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

## Step 3：拟南芥稳定遗传转化（花序浸染法）
### 3.1 原理
花序浸染法（Floral dip method）是拟南芥稳定转化的标准方法，由Clough & Bent（1998）建立。将含过表达载体的农杆菌菌液直接浸染拟南芥花序，农杆菌侵染花序中的雌配子体（卵细胞），T-DNA整合至生殖细胞基因组，从而在T1代种子中获得转基因植株。该方法无需组织培养，操作极为简便。

### 3.2 材料准备
- **菌株：** A. tumefaciens GV3101（pMP90）（含过表达载体）
- **拟南芥生态型：** Col-0（哥伦比亚，标准生态型）
- **植株状态：** 主茎花序刚开始抽出，第一批花蕾出现时（约4-5周龄）

### 3.3 农杆菌制备
1.  将含过表达载体的GV3101划线至含抗生素的YEB平板：
    - 利福平（Rifampicin）：25 μg/mL
    - 庆大霉素（Gentamycin）：25 μg/mL（GV3101自身抗性）
    - 载体抗生素（如卡那霉素：50 μg/mL）
2.  28°C培养2天
3.  挑单菌落接种至5 mL YEB液体培养基，28°C过夜
4.  转接至200-500 mL YEB，培养至OD₆₀₀ = 0.8-1.0（约12-16 h）
5.  4000 rpm离心10 min，弃上清
6.  用浸染缓冲液重悬至OD₆₀₀ = 0.8：
    - 浸染缓冲液：5% 蔗糖 + 0.02% Silwet L-77（表面活性剂）
7.  室温静置30 min

### 3.4 花序浸染操作
**植株准备（浸染前1-2天）：**

1.  选取主茎花序刚抽出、第一批花蕾出现的植株（约4-5周龄）
2.  剪去已开放的花朵和角果（保留花蕾）
3.  浸染前一天停止浇水（使植株轻度缺水，有助于提高转化效率）

**Day 0 — 浸染：**
1.  将菌液倒入烧杯或培养皿（足够浸没花序）
2.  将拟南芥植株倒置，使花序完全浸入菌液中
3.  轻柔摇动，浸染30-60 sec
4.  取出植株，用无菌纸巾轻轻吸去多余菌液
5.  将植株侧放（水平放置），套袋保湿，黑暗处理24 h
6.  次日去袋，恢复正常生长条件（22°C，16 h光照/8 h黑暗）

**重复浸染（可选）：**
- 7天后重复浸染一次，可提高转化效率

**种子收获：**
- 浸染后约4-5周，待角果成熟（变黄）时收获种子
- 将整株植株装入纸袋，自然干燥后脱粒
- 4°C干燥保存T1代种子

**转化效率参考：** 花序浸染法T1代种子中阳性率约0.1-1%，每次实验建议处理5-10株植株，收获约1000-5000粒T1代种子。

## Step 4：T1代种子筛选与PCR鉴定
### 4.1 T1代种子抗性筛选
**目的：** 从大量T1代种子中筛选出含T-DNA的转基因植株
**方法一：培养基抗性筛选（推荐）**
1.  T1代种子表面消毒：
    - 70%乙醇：1 min
    - 10% NaClO（含0.1% Tween-20）：10 min
    - 无菌水洗涤5次
2.  4°C春化处理2-3天（打破休眠）
3.  播种至含筛选抗生素的MS培养基：
    - 卡那霉素（50 mg/L）：用于含NPTII基因的载体
    - 潮霉素（25 mg/L）：用于含Hyg基因的载体
4.  22°C，16 h光照/8 h黑暗培养7-10天
5.  筛选标准：
    - 阳性植株（含T-DNA）：子叶绿色，根系正常生长
    - 阴性植株：子叶黄化，根系生长受抑
6.  将阳性幼苗移栽至土壤，正常管理

**方法二：土壤直接筛选（BASTA/草丁膦）**
- 适用于含bar基因的载体
- 将T1代种子直接播种至土壤
- 待子叶展开后，喷施0.1% BASTA（草丁膦）溶液
- 阳性植株存活，阴性植株死亡

### 4.2 T1植株基因组DNA提取
**材料：** 约50-100 mg新鲜叶片（或CTAB快速提取法）
**快速CTAB法（适合大量样品）：**
1.  取约50 mg叶片，加入200 μL 2× CTAB缓冲液
2.  65°C水浴10 min
3.  加入200 μL 氯仿:异戊醇（24:1），充分混匀
4.  12000 rpm离心5 min，取上清（约150 μL）
5.  加入等体积异丙醇，混匀，室温静置5 min
6.  12000 rpm离心5 min，弃上清
7.  70%乙醇洗涤，室温晾干
8.  溶于50 μL TE缓冲液
9.  NanoDrop定量

### 4.3 T1植株PCR鉴定
- 正向引物：位于35S启动子
- 反向引物：位于目的基因外显子区域
以上述基因组DNA为模板，通过PCR扩增及凝胶电泳检测，对T0转基因植物进行阳性材料鉴定。

PCR程序：
1.  95°C 3 min
2.  30个循环：95°C 30 sec / 58-60°C 30 sec / 72°C 30 sec
3.  72°C 5 min
4.  4°C 保存

## Step 5：qPCR定量确定目标基因表达水平
### 5.1 目标基因表达水平qPCR定量分析
（A）提取T1代阳性植株Total RNA，并反转录成第一链cDNA。
（B）设计目标基因qPCR定量引物，长度约90~200 bp。
（C）以cDNA为模板，对目标基因进行qPCR定量分析。
（D）挑选满足实验需求的不同表达水平的株系进行T2代表型鉴定。
- 拟南芥内参基因：AtActin2（AT3G18780）或 AtUBQ10（AT4G05320）。
（E）表型观察：
- 记录与野生型相比的表型差异
- 农艺性状评估（株高、株型、果荚等）

### 5.2 株系命名与保存
**命名规则：**[T0株系号]-[T1株系号]- [启动子]-[基因名]
例： T0-3-T1-7-P35S-Atxxx

**保存：**
- 收取T1纯合编辑植株种子（T2代种子）
- 每株系保存至少50粒种子，-20°C干燥保存
- 建立株系档案（基因型、表型、测序结果）