const fs = require('fs');
const path = require('path');

function walk(dir) {
    let results = [];
    const list = fs.readdirSync(dir);
    list.forEach(file => {
        file = path.join(dir, file);
        const stat = fs.statSync(file);
        if (stat && stat.isDirectory()) {
            results = results.concat(walk(file));
        } else {
            if (file.endsWith('.ts') || file.endsWith('.tsx')) {
                results.push(file);
            }
        }
    });
    return results;
}

const files = walk('.');

files.forEach(file => {
    let content = fs.readFileSync(file, 'utf8');
    let original = content;

    // Replace single quote strings
    content = content.replace(/'http:\/\/127\.0\.0\.1:8000\//g, '`${process.env.NEXT_PUBLIC_API_URL || \'http://127.0.0.1:8000\'}/');
    
    // Replace double quote strings
    content = content.replace(/"http:\/\/127\.0\.0\.1:8000\//g, '`${process.env.NEXT_PUBLIC_API_URL || \'http://127.0.0.1:8000\'}/');

    // Replace template literals
    content = content.replace(/`http:\/\/127\.0\.0\.1:8000\//g, '`${process.env.NEXT_PUBLIC_API_URL || \'http://127.0.0.1:8000\'}/');
    
    // Replace websocket URL specifically in useSocket.ts
    content = content.replace(/'ws:\/\/127\.0\.0\.1:8000\/ws'/g, 'process.env.NEXT_PUBLIC_WS_URL || \'ws://127.0.0.1:8000/ws\'');

    if (content !== original) {
        fs.writeFileSync(file, content);
        console.log(`Updated ${file}`);
    }
});
console.log("Done");
