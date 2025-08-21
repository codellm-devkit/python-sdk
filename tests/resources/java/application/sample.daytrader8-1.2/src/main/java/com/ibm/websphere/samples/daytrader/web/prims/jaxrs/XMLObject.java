/**
 * (C) Copyright IBM Corporation 2019.
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
package com.ibm.websphere.samples.daytrader.web.prims.jaxrs;

import javax.xml.bind.annotation.XmlRootElement;

/**
 * with @XmlRootElement, make the XMLObject as a JAXB object
 * then add/remove any atteribute with setter& getter
 * 
 * note: please change all XMLObjects in project JAXRSJ2SEClient,JAXRSBenchService,JAXRS20Client
 * they should share the same XMLObject
 * @author alexzan
 *
 */
@XmlRootElement
public class XMLObject {

  private String prop0001;
  private String prop0002;    
  private String prop0003;
  private String prop0004;
  private String prop0005;
  private String prop0006;
  private String prop0007;
  private String prop0008;
  private String prop0009;
  private String prop0010;
  private String prop0011;
  private String prop0012;
  private String prop0013;
  private String prop0014;
  private String prop0015;
  private String prop0016;
  private String x;
  
  public String getProp0001() {
    return prop0001;
  }
  public void setProp0001(String prop0001) {
    this.prop0001 = prop0001;
  }
  public String getProp0002() {
    return prop0002;
  }
  public void setProp0002(String prop0002) {
    this.prop0002 = prop0002;
  }
  public String getProp0003() {
    return prop0003;
  }
  public void setProp0003(String prop0003) {
    this.prop0003 = prop0003;
  }
  public String getProp0004() {
    return prop0004;
  }
  public void setProp0004(String prop0004) {
    this.prop0004 = prop0004;
  }
  public String getProp0005() {
    return prop0005;
  }
  public void setProp0005(String prop0005) {
    this.prop0005 = prop0005;
  }
  public String getProp0006() {
    return prop0006;
  }
  public void setProp0006(String prop0006) {
    this.prop0006 = prop0006;
  }
  public String getProp0007() {
    return prop0007;
  }
  public void setProp0007(String prop0007) {
    this.prop0007 = prop0007;
  }
  public String getProp0008() {
    return prop0008;
  }
  public void setProp0008(String prop0008) {
    this.prop0008 = prop0008;
  }
  public String getProp0009() {
    return prop0009;
  }
  public void setProp0009(String prop0009) {
    this.prop0009 = prop0009;
  }
  public String getProp0010() {
    return prop0010;
  }
  public void setProp0010(String prop0010) {
    this.prop0010 = prop0010;
  }
  public String getProp0011() {
    return prop0011;
  }
  public void setProp0011(String prop0011) {
    this.prop0011 = prop0011;
  }
  public String getProp0012() {
    return prop0012;
  }
  public void setProp0012(String prop0012) {
    this.prop0012 = prop0012;
  }
  public String getProp0013() {
    return prop0013;
  }
  public void setProp0013(String prop0013) {
    this.prop0013 = prop0013;
  }
  public String getProp0014() {
    return prop0014;
  }
  public void setProp0014(String prop0014) {
    this.prop0014 = prop0014;
  }
  public String getProp0015() {
    return prop0015;
  }
  public void setProp0015(String prop0015) {
    this.prop0015 = prop0015;
  }
  public String getProp0016() {
    return prop0016;
  }
  public void setProp0016(String prop0016) {
    this.prop0016 = prop0016;
  }
  public String getX() {
    return x;
  }
  public void setX(String x) {
    this.x = x;
  }
}
