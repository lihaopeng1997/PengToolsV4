// -*- coding: utf-8 -*-
/**
 * 每日经典句（Daily Classic）：
 * 纯本地公版古典名句数据源，严禁外部网络请求与本地持久化，严格按日期确定性映射（同一日期永不变化）。
 */

export interface DailyQuote {
  text: string
  author: string
  source: string
}

export const DAILY_QUOTES: readonly DailyQuote[] = [
  { text: '行到水穷处，坐看云起时。', author: '王维', source: '《终南别业》' },
  { text: '博观而约取，厚积而薄发。', author: '苏轼', source: '《稼说送张琥》' },
  { text: '长风破浪会有时，直挂云帆济沧海。', author: '李白', source: '《行路难》' },
  { text: '海不辞水，故能成其大；山不辞土石，故能成其高。', author: '管子', source: '《管子·形势解》' },
  { text: '不积跬步，无以至千里；不积小流，无以成江海。', author: '荀子', source: '《劝学》' },
  { text: '千里之行，始于足下。', author: '老子', source: '《道德经》' },
  { text: '操千曲而后晓声，观千剑而后识器。', author: '刘勰', source: '《文心雕龙》' },
  { text: '工欲善其事，必先利其器。', author: '孔子', source: '《论语·卫灵公》' },
  { text: '沉舟侧畔千帆过，病树前头万木春。', author: '刘禹锡', source: '《酬乐天扬州初逢席上见赠》' },
  { text: '纸上得来终觉浅，绝知此事要躬行。', author: '陆游', source: '《冬夜读书示子聿》' },
  { text: '路漫漫其修远兮，吾将上下而求索。', author: '屈原', source: '《离骚》' },
  { text: '穷则变，变则通，通则久。', author: '《易经》', source: '《易·系辞下》' },
  { text: '锲而舍之，朽木不折；锲而不舍，金石可镂。', author: '荀子', source: '《劝学》' },
  { text: '落霞与孤鹜齐飞，秋水共长天一色。', author: '王勃', source: '《滕王阁序》' },
  { text: '会当凌绝顶，一览众山小。', author: '杜甫', source: '《望岳》' },
  { text: '山重水复疑无路，柳暗花明又一村。', author: '陆游', source: '《游山西村》' },
  { text: '天行健，君子以自强不息。', author: '《易经》', source: '《易·乾》' },
  { text: '见贤思齐焉，见不贤而内自省也。', author: '孔子', source: '《论语·里仁》' },
  { text: '九层之台，起于累土；合抱之木，生于毫末。', author: '老子', source: '《道德经》' },
  { text: '敏而好学，不耻下问。', author: '孔子', source: '《论语·公冶长》' },
  { text: '知之者不如好之者，好之者不如乐之者。', author: '孔子', source: '《论语·雍也》' },
  { text: '非淡泊无以明志，非宁静无以致远。', author: '诸葛亮', source: '《诫子书》' },
  { text: '日月不肯迟，四时相催迫。', author: '陶渊明', source: '《杂诗》' },
  { text: '业精于勤，荒于嬉；行成于思，毁于随。', author: '韩愈', source: '《进学解》' },
  { text: '问渠那得清如许？为有源头活水来。', author: '朱熹', source: '《观书有感》' },
  { text: '居高声自远，非是藉秋风。', author: '虞世南', source: '《蝉》' },
  { text: '潮平两岸阔，风正一帆悬。', author: '王湾', source: '《次北固山下》' },
  { text: '莫道桑榆晚，为霞尚满天。', author: '刘禹锡', source: '《酬乐天咏老见示》' },
  { text: '星垂平野阔，月涌大江流。', author: '杜甫', source: '《旅夜书怀》' },
  { text: '志不立，天下无可成之事。', author: '王阳明', source: '《教条示龙场诸生》' },
]

/**
 * 根据 YYYY-MM-DD 字符串哈希映射为稳定下标，纯确定性算法。
 */
function hashDate(dateStr: string): number {
  let hash = 0
  for (let i = 0; i < dateStr.length; i++) {
    hash = ((hash << 5) - hash) + dateStr.charCodeAt(i)
    hash |= 0
  }
  return Math.abs(hash)
}

/**
 * 根据传入日期（或当前本地日期）获取确定性名句。
 */
export function getDailyQuote(dateKey?: string): DailyQuote {
  const key = dateKey || (new Date()).toISOString().slice(0, 10)
  const index = hashDate(key) % DAILY_QUOTES.length
  return DAILY_QUOTES[index]
}
