/**
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.web.prims;

import java.io.Serializable;

/**
 *
 * An object that contains approximately 1024 bits of information. This is used
 * by {@link PingSession3}
 *
 */
public class PingSession3Object implements Serializable {
    // PingSession3Object represents a BLOB of session data of various.
    // Each instantiation of this class is approximately 1K in size (not
    // including overhead for arrays and Strings)
    // Using different datatype exercises the various serialization algorithms
    // for each type

    private static final long serialVersionUID = 1452347702903504717L;
    byte[] byteVal = new byte[16]; // 8 * 16 = 128 bits
    char[] charVal = new char[8]; // 16 * 8 = 128 bits
    int a, b, c, d; // 4 * 32 = 128 bits
    float e, f, g, h; // 4 * 32 = 128 bits
    double i, j; // 2 * 64 = 128 bits
    // Primitive type size = ~5*128= 640

    String s1 = new String("123456789012");
    String s2 = new String("abcdefghijkl");

    // String type size = ~2*12*16 = 384
    // Total blob size (w/o overhead) = 1024

    // The Session blob must be filled with data to avoid compression of the
    // blob during serialization
    PingSession3Object() {
        int index;
        byte b = 0x8;
        for (index = 0; index < 16; index++) {
            byteVal[index] = (byte) (b + 2);
        }

        char c = 'a';
        for (index = 0; index < 8; index++) {
            charVal[index] = (char) (c + 2);
        }

        a = 1;
        b = 2;
        c = 3;
        d = 5;
        e = (float) 7.0;
        f = (float) 11.0;
        g = (float) 13.0;
        h = (float) 17.0;
        i = 19.0;
        j = 23.0;
    }
    /**
     * Main method to test the serialization of the Session Data blob object
     * Creation date: (4/3/2000 3:07:34 PM)
     *
     * @param args
     *            java.lang.String[]
     */

    /**
     * Since the following main method were written for testing purpose, we
     * comment them out public static void main(String[] args) { try {
     * PingSession3Object data = new PingSession3Object();
     *
     * FileOutputStream ostream = new
     * FileOutputStream("c:\\temp\\datablob.xxx"); ObjectOutputStream p = new
     * ObjectOutputStream(ostream); p.writeObject(data); p.flush();
     * ostream.close(); } catch (Exception e) { System.out.println("Exception: "
     * + e.toString()); } }
     */

}