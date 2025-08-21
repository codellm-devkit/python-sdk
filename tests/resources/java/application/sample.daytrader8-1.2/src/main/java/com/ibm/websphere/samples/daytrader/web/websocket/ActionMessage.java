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
package com.ibm.websphere.samples.daytrader.web.websocket;

import java.io.StringReader;

import javax.json.Json;
import javax.json.stream.JsonParser;

import com.ibm.websphere.samples.daytrader.util.Log;

/**
 *  Licensed to the Apache Software Foundation (ASF) under one or more
 *  contributor license agreements.  See the NOTICE file distributed with
 *  this work for additional information regarding copyright ownership.
 *  The ASF licenses this file to You under the Apache License, Version 2.0
 *  (the "License"); you may not use this file except in compliance with
 *  the License.  You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 *  Unless required by applicable law or agreed to in writing, software
 *  distributed under the License is distributed on an "AS IS" BASIS,
 *  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *  See the License for the specific language governing permissions and
 *  limitations under the License.
 */
public class ActionMessage {

  String decodedAction = null;

  public ActionMessage() {  
  }

  public void doDecoding(String jsonText) {

    String keyName = null;
    try 
    {
      // JSON parse
      JsonParser parser = Json.createParser(new StringReader(jsonText));
      while (parser.hasNext()) {
        JsonParser.Event event = parser.next();
        switch(event) {
        case KEY_NAME:
          keyName=parser.getString();
          break;
        case VALUE_STRING:
          if (keyName != null && keyName.equals("action")) {
            decodedAction=parser.getString();
          }
          break;
        default:
          break;
        }
      }
    } catch (Exception e) {
      Log.error("ActionMessage:doDecoding(" + jsonText + ") --> failed", e);
    }


    Log.trace("ActionMessage:doDecoding -- decoded action -->" + decodedAction + "<--");

  }


public String getDecodedAction() {
  return decodedAction;
}

}

