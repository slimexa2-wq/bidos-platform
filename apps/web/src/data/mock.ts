import type { AlertItem, BidBondRecord, BidDocument, Competitor, DashboardSummary, EvaluationCase, Opportunity, RegistrationCase, ResultReview, SearchResult, SourcePointer } from '../types/domain'

export const sourcePointers: SourcePointer[] = [
  { id:'src-official-1', type:'official', label:'四川政府采购网原公告', url:'https://example.com/official/001', capturedAt:'2026-09-24 08:10', trust:'official' },
  { id:'src-sheet-1', type:'sheet', label:'盲投信息表对应行', url:'https://example.com/sheet/row/206', location:'2026年未参与 / 第206行', trust:'internal' },
  { id:'src-file-1', type:'file', label:'招标文件.pdf', url:'https://example.com/files/tender.pdf', location:'P12 / 第4.2条', trust:'official' },
  { id:'src-bid-1', type:'official', label:'中标公告', url:'https://example.com/award/001', trust:'official' },
  { id:'src-comp-1', type:'sheet', label:'竞对数据表对应记录', url:'https://example.com/competitors/baohang', location:'宝航 / 2026年项目', trust:'internal' },
]

const projectSources = [sourcePointers[0], sourcePointers[1], sourcePointers[2]]

export const opportunities: Opportunity[] = [
  { id:'p1', name:'XX集团劳务外包项目', client:'XX集团', region:'成都', budget:12000000, deadline:'2026-09-28 18:00', stage:'报名管理', status:'今日截止', severity:'critical', projectType:'劳务外包', sourceName:'四川政府采购网', summary:'约300-350人，公开招标，服务期3年；公告与招标文件金额存在差异。', followed:true, verified:false, sources:projectSources },
  { id:'p2', name:'XX电子产业园保洁项目', client:'XX电子产业园', region:'绵阳', budget:6800000, deadline:'2026-10-02 09:30', stage:'摘要评估', status:'待人工确认', severity:'warning', projectType:'后勤服务', sourceName:'中国招标投标公共服务平台', summary:'评分规则中方案分占比较高，近年竞对活跃。', followed:true, verified:true, sources:[sourcePointers[0], sourcePointers[2]] },
  { id:'p3', name:'XX市医院后勤服务项目', client:'XX市医院', region:'成都', budget:9500000, deadline:'2026-10-05 17:00', stage:'标书制作', status:'进行中', severity:'info', projectType:'后勤服务', sourceName:'四川公共资源交易网', summary:'已完成报名，标书技术部分仍有两处材料缺失。', followed:true, verified:true, sources:projectSources },
  { id:'p4', name:'XX区教育系统后勤服务项目', client:'XX区教育局', region:'成都', budget:6230000, deadline:'2026-09-24', stage:'结果复盘', status:'中标结果', severity:'success', projectType:'后勤服务', sourceName:'四川政府采购网', summary:'已公布中标结果，可回看当时判断与竞对表现。', followed:false, verified:true, sources:[sourcePointers[3], sourcePointers[1]] },
  { id:'p5', name:'XX产业园物业服务项目', client:'XX产业园', region:'宜宾', budget:9800000, deadline:'2026-10-12', stage:'盲投信息', status:'新发现', severity:'success', projectType:'物业服务', sourceName:'中国招标投标公共服务平台', summary:'与公司历史4个项目高度相似，建议优先核验。', followed:false, verified:false, sources:[sourcePointers[0], sourcePointers[1]] },
  { id:'p6', name:'XX机构食堂劳务项目', client:'XX机构', region:'泸州', budget:4500000, deadline:'2026-09-30', stage:'盲投信息', status:'待核验', severity:'warning', projectType:'劳务外包', sourceName:'四川政府采购网', summary:'预算信息缺失，需要核验附件。', followed:false, verified:false, sources:projectSources },
]

export const dashboard: DashboardSummary = {
  stages: [
    { name:'盲投信息', primary:18, primaryLabel:'今日新增', secondary:3, secondaryLabel:'待核验', path:'/blind' },
    { name:'报名管理', primary:5, primaryLabel:'进行中', secondary:1, secondaryLabel:'今日截止', path:'/registration' },
    { name:'摘要评估', primary:3, primaryLabel:'待评估', secondary:2, secondaryLabel:'待人工确认', path:'/evaluation' },
    { name:'标书制作', primary:4, primaryLabel:'进行中', secondary:2, secondaryLabel:'48小时内截止', path:'/document' },
    { name:'结果复盘', primary:3, primaryLabel:'新增结果', secondary:2, secondaryLabel:'待复盘', path:'/results' },
  ],
  keyTimeline: [
    { id:'t1', at:'今天 18:00', label:'XX集团劳务外包项目报名截止', projectId:'p1', severity:'critical', action:'去报名' },
    { id:'t2', at:'10月2日 09:30', label:'XX电子产业园项目开标', projectId:'p2', severity:'warning', action:'查看详情' },
    { id:'t3', at:'10月5日 17:00', label:'XX市医院后勤服务项目提交截止', projectId:'p3', severity:'info', action:'去处理' },
    { id:'t4', at:'持续提醒', label:'宝航服务的XX生产辅助项目距结束77天，尚未发现新招标公告', severity:'warning', action:'持续跟踪', path:'/results', persistent:true },
    { id:'t5', at:'已逾期7天', label:'天府新区建工大厦项目投标保证金60,000元尚未确认退回', severity:'critical', action:'保证金台账', path:'/bid-bonds', persistent:true },
    { id:'t6', at:'10月8日', label:'XX区物业服务项目开标', severity:'warning', action:'查看详情', path:'/results' },
  ]
}

export const alerts: AlertItem[] = [
  { id:'a1', title:'金额信息存在不一致', projectId:'p1', detail:'官网公告1,200万，但招标文件中为980万，需要人工核验。', timeLabel:'10分钟前', severity:'critical', actionLabel:'核验', handled:false, sourceIds:['src-official-1','src-file-1','src-sheet-1'] },
  { id:'a2', title:'新增更正公告', projectId:'p2', detail:'新增3项人员资质要求，投标截止由10月8日调整至10月12日。', timeLabel:'2小时前', severity:'warning', actionLabel:'查看变化', handled:false, sourceIds:['src-official-1'] },
  { id:'a3', title:'中标结果已发布', projectId:'p4', detail:'已自动匹配中标单位与金额，并写回结果记录。', timeLabel:'5小时前', severity:'success', actionLabel:'去复盘', handled:false, sourceIds:['src-bid-1','src-sheet-1'] },
  { id:'a4', title:'竞对项目进入90天到期窗口', detail:'宝航服务的XX生产辅助项目距结束77天；在匹配到新招标公告、续签确认或人工关闭前持续提醒。', timeLabel:'持续提醒', severity:'warning', actionLabel:'持续跟踪', handled:false, persistent:true, resolutionHint:'新公告匹配 / 已续签 / 人工关闭', sourceIds:['src-comp-1'] },
  { id:'a5', title:'投标保证金逾期未退', projectId:'p3', detail:'天府新区建工大厦项目投标保证金60,000元已超过应退日期7天，尚未确认到账。', timeLabel:'持续提醒', severity:'critical', actionLabel:'查看台账', handled:false, persistent:true, resolutionHint:'确认保证金已退回', sourceIds:['src-sheet-1'] },
]

export const registrations: RegistrationCase[] = [
  { projectId:'p1', progress:63, deadline:'今天 18:00', status:'报名准备', materials:[
    { id:'m1', label:'营业执照', status:'ready', source:'公司资质库' },
    { id:'m2', label:'劳务派遣经营许可证', status:'ready', source:'公司资质库' },
    { id:'m3', label:'法人身份证明', status:'ready', source:'公司资质库' },
    { id:'m4', label:'近三年类似业绩证明', status:'missing' },
    { id:'m5', label:'财务报表', status:'ready', source:'财务资料库' },
    { id:'m6', label:'项目负责人及人员证书', status:'confirm' },
    { id:'m7', label:'保证金缴纳凭证', status:'unpaid' },
    { id:'m8', label:'其他补充材料', status:'confirm' },
  ]},
  { projectId:'p2', progress:82, deadline:'10月2日', status:'资料整理', materials:[
    { id:'m9', label:'基础报名资料', status:'ready' }, { id:'m10', label:'业绩证明', status:'ready' }, { id:'m11', label:'人员证书', status:'confirm' }
  ]},
]

export const evaluations: EvaluationCase[] = [
  { projectId:'p1', score:72, aiView:'建议参与，但必须先核验预算金额差异和新增人员资质要求；AI仅提供数据与参考思路。', humanDecision:'待讨论', humanNote:'', evidence:[
    { id:'e1', type:'support', text:'过去3年有4个相似项目，其中2个中标；项目类型与公司优势业务一致。', sourceLabel:'历史项目库' },
    { id:'e2', type:'support', text:'评分中价格权重约30%，并非纯低价竞争。', sourceLabel:'招标文件 P42' },
    { id:'e3', type:'caution', text:'官网预算1,200万与招标文件980万存在差异。', sourceLabel:'原公告 + 招标文件 P12' },
    { id:'e4', type:'caution', text:'新增3项人员资质要求，需要确认是否满足。', sourceLabel:'更正公告' },
    { id:'e5', type:'unknown', text:'主要竞对是否参与尚未确认。', sourceLabel:'竞对监测' },
  ], similarProjects:[
    { name:'XX电子产业园后勤服务项目', year:'2024', result:'中标', similarity:92 },
    { name:'XX市医院后勤服务项目', year:'2023', result:'失标', similarity:88 },
    { name:'XX园区物业服务项目', year:'2023', result:'未参与', similarity:85 },
  ]}
]

export const bidDocuments: BidDocument[] = [
  { projectId:'p3', progress:72, dueAt:'2天后', sections:[
    { id:'s1', title:'封面及目录', progress:100, status:'done', content:'封面、目录与项目基本信息。' },
    { id:'s2', title:'商务部分', progress:100, status:'done', content:'投标函、法人授权书、企业资质、业绩证明。' },
    { id:'s3', title:'项目理解', progress:100, status:'done', content:'项目背景、目标与范围。' },
    { id:'s4', title:'服务方案', progress:80, status:'working', content:'围绕人员招聘与配置、在岗管理、离职管理、异常处理建立标准化服务机制。' },
    { id:'s5', title:'人员配置方案', progress:60, status:'working', content:'根据甲方需求与岗位结构配置人员。' },
    { id:'s6', title:'安全管理方案', progress:0, status:'todo', content:'' },
    { id:'s7', title:'报价文件', progress:50, status:'working', content:'报价说明与费用构成。' },
    { id:'s8', title:'其他附件', progress:100, status:'done', content:'相关承诺与附件。' },
  ], issues:[
    { id:'i1', severity:'critical', text:'第28页人员数量300人与第63页人员配置表280人不一致', location:'P28 / P63' },
    { id:'i2', severity:'warning', text:'缺少近三年类似项目业绩证明1项', location:'商务部分 > 业绩证明' },
  ]}
]

export const results: ResultReview[] = [
  { projectId:'p1', result:'未中标', resultDate:'2026-09-20', projectEndDate:'2026-12-10', projectEndDateKind:'exact', expiryResolved:false, winner:'宝航人力', winningPrice:915, ourPrice:960, reasons:['报价高于中标单位45万（约4.7%）','商务分低于中标单位4.5分','人员配置方案与甲方实际需求匹配度不足'], lessons:['加强前期价格测算','补充相似项目经验','对竞对近期报价趋势建立长期参考'], reviewed:false },
  { projectId:'p4', result:'中标', resultDate:'2026-09-05', projectEndDate:'2026-12-22', projectEndDateKind:'exact', expiryResolved:false, winner:'我司', winningPrice:623, ourPrice:623, reasons:['商务资质匹配','历史类似项目经验充分'], lessons:['沉淀本次技术方案作为可复用案例','项目结束前90天持续关注续签/重新招标'], reviewed:true },
  { projectId:'p5', result:'放弃', resultDate:'2026-08-28', projectEndDate:'2027-08-28', projectEndDateKind:'estimated', expiryResolved:false, winner:'', winningPrice:null, ourPrice:null, reasons:['项目利润空间不足'], lessons:['保留该类项目成本测算参数'], reviewed:true },
]

export const bidBonds: BidBondRecord[] = [
  { id:'bb1', projectId:'p3', projectName:'天府新区建工大厦物业劳务外包服务采购项目', amount:60000, paidAt:'2026-09-12', resultDate:'2026-09-17', expectedRefundAt:'2026-09-17', status:'逾期未退', owner:'陈端', overdueDays:7, sourceLabel:'投标项目计划 / 保证金台账', note:'系统持续提醒，直到财务确认到账。' },
  { id:'bb2', projectId:'p2', projectName:'2026年宜宾宜瑞汽车有限公司采购人力资源公司项目', amount:5000, paidAt:'2026-09-12', resultDate:'2026-09-15', expectedRefundAt:'2026-09-27', status:'待退回', owner:'张思琦', overdueDays:0, sourceLabel:'投标项目计划 / 招标文件退还条款' },
  { id:'bb3', projectId:'p6', projectName:'广元市国正人力资源有限公司劳务服务合作单位采购', amount:20000, paidAt:'2026-09-15', resultDate:'2026-09-18', expectedRefundAt:'2026-09-25', status:'待退回', owner:'陈端', overdueDays:0, sourceLabel:'投标项目计划 / 招标文件退还条款' },
  { id:'bb4', projectId:'p4', projectName:'XX区教育系统后勤服务项目', amount:30000, paidAt:'2026-08-28', resultDate:'2026-09-05', expectedRefundAt:'2026-09-10', actualRefundAt:'2026-09-09', status:'已退回', owner:'张三', overdueDays:0, sourceLabel:'财务到账记录' },
]

export const competitors: Competitor[] = [
  { id:'c1', name:'宝航人力资源有限公司', regions:['四川','重庆','贵州'], projectCount:68, wonCount:32, avgQuoteRatio:0.947, activity:'活跃', specialties:['劳务派遣','劳务外包','物业服务'], expiringProjects:3, recentTrend:[5,8,8,12,10,15,10,8,7,10,13,9] },
  { id:'c2', name:'中智人力资源有限公司', regions:['全国'], projectCount:56, wonCount:21, avgQuoteRatio:0.956, activity:'活跃', specialties:['人力资源外包','派遣'], expiringProjects:2, recentTrend:[4,6,7,8,11,10,9,7,8,9,10,11] },
  { id:'c3', name:'四川佳和人力服务有限公司', regions:['四川','云南'], projectCount:42, wonCount:15, avgQuoteRatio:0.962, activity:'一般', specialties:['劳务派遣'], expiringProjects:1, recentTrend:[2,3,5,6,4,5,4,3,5,4,6,5] },
  { id:'c4', name:'华信劳务服务有限公司', regions:['四川','重庆'], projectCount:38, wonCount:12, avgQuoteRatio:0.951, activity:'活跃', specialties:['后勤服务','劳务外包'], expiringProjects:2, recentTrend:[3,4,5,5,7,6,4,5,6,7,8,7] },
]

export const searchResults: SearchResult[] = [
  { id:'sr1', type:'project', title:'XX集团2024年劳务外包项目', snippet:'中标金额589万，中标单位宝航；服务期2年。', source:sourcePointers[3], score:.94 },
  { id:'sr2', type:'sheet', title:'2026年盲投未参与项目记录', snippet:'未参与原因：垫资周期长；最终中标单位为同行公司。', source:sourcePointers[1], score:.91 },
  { id:'sr3', type:'competitor', title:'宝航人力资源有限公司', snippet:'近12个月参与68个项目，中标32个，成都地区活动较高。', source:sourcePointers[4], score:.88 },
  { id:'sr4', type:'file', title:'历史项目复盘：价格因素', snippet:'我司报价高于中标单位约4.7%，商务分同时存在差距。', source:sourcePointers[2], score:.85 },
]
