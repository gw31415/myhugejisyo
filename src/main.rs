// SPDX-License-Identifier: GPL-3.0-only
use rustmigemo::migemo::{
    compact_dictionary::CompactDictionary, compact_dictionary_builder, query::query,
    regex_generator::RegexOperator,
};
use std::{
    collections::HashMap,
    error::Error,
    fs,
    io::{BufRead, BufReader},
    time::Instant,
};

fn supported(s: &str) -> bool {
    !s.is_empty()
        && s.chars()
            .all(|c| matches!(c, ' '..='~' | '\u{3041}'..='\u{3096}' | 'ー'))
}
fn pairs(path: &str) -> Result<HashMap<String, Vec<String>>, Box<dyn Error>> {
    let mut map: HashMap<String, Vec<String>> = HashMap::new();
    for (i, line) in BufReader::new(fs::File::open(path)?).lines().enumerate() {
        let line = line?;
        let (reading, surface) = line
            .split_once('\t')
            .ok_or(format!("line {}: expected reading TAB surface", i + 1))?;
        if !supported(reading) || surface.is_empty() || surface.chars().any(char::is_control) {
            return Err(format!("line {}: unsupported reading or surface", i + 1).into());
        }
        map.entry(reading.into()).or_default().push(surface.into());
    }
    for v in map.values_mut() {
        v.sort();
        v.dedup();
    }
    if map.is_empty() {
        return Err("dictionary is empty".into());
    }
    Ok(map)
}
fn verify(
    dict: &CompactDictionary,
    expected: &HashMap<String, Vec<String>>,
) -> Result<(), Box<dyn Error>> {
    for (key, values) in expected {
        let key16 = key.encode_utf16().collect();
        let mut actual: Vec<String> = dict
            .search(&key16)
            .map(|v| String::from_utf16(&v))
            .collect::<Result<_, _>>()?;
        actual.sort();
        if &actual != values {
            return Err(format!("round-trip mismatch: {key}").into());
        }
    }
    Ok(())
}
fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().collect();
    let start = Instant::now();
    match args.get(1).map(String::as_str) {
        Some("build") if args.len() == 4 => {
            let map = pairs(&args[2])?;
            let keys = map.len();
            let count: usize = map.values().map(Vec::len).sum();
            eprintln!("building {keys} readings / {count} pairs");
            let bytes = compact_dictionary_builder::build(map);
            let dict = CompactDictionary::new(&bytes);
            // Re-read input instead of retaining a second full map during trie construction.
            verify(&dict, &pairs(&args[2])?)?;
            let tmp = format!("{}.tmp", args[3]);
            fs::write(&tmp, &bytes)?;
            fs::rename(tmp, &args[3])?;
            eprintln!("verified every pair; {} bytes; {:.3}s", bytes.len(), start.elapsed().as_secs_f64());
        }
        Some("lookup") if args.len() == 4 => {
            let bytes = fs::read(&args[2])?;
            let dict = CompactDictionary::new(&bytes);
            for value in dict.search(&args[3].encode_utf16().collect()) { println!("{}", String::from_utf16(&value)?); }
        }
        Some("query") if args.len() == 4 || args.len() == 5 => {
            let bytes = fs::read(&args[2])?;
            let dict = CompactDictionary::new(&bytes);
            let before = Instant::now();
            let pattern = query(args[3].clone(), &dict, &RegexOperator::Default);
            let generation = before.elapsed();
            let before = Instant::now();
            let re = regex::Regex::new(&pattern)?;
            eprintln!("regex_bytes={} generation_ms={:.3} compile_ms={:.3}", pattern.len(), generation.as_secs_f64()*1000., before.elapsed().as_secs_f64()*1000.);
            if let Some(text) = args.get(4) {
                if !re.is_match(text) { return Err(format!("query did not match: {text}").into()); }
                println!("matched: {text}");
            } else { println!("{pattern}"); }
        }
        _ => return Err("usage: myhugejisyo build INPUT.tsv OUTPUT | lookup DICT READING | query DICT ROMAJI [EXPECTED_TEXT]".into())
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn binary_roundtrip_unicode_and_determinism() {
        let map = HashMap::from([
            ("けんさく".into(), vec!["検索".into(), "𠮷検索".into()]),
            ("えーあい".into(), vec!["AI".into()]),
        ]);
        let a = compact_dictionary_builder::build(map.clone());
        assert_eq!(a, compact_dictionary_builder::build(map.clone()));
        verify(&CompactDictionary::new(&a), &map).unwrap();
        assert!(!supported("カナ"));
        assert!(!supported("ゔ\u{3099}"));
    }
}
